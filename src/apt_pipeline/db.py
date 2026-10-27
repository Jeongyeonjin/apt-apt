"""DB 접근. 운영은 PostgreSQL, 로컬 테스트는 SQLite.

두 DB 모두 'INSERT ... ON CONFLICT DO UPDATE'를 지원하므로 같은 SQL을 쓰고,
값 자리표시자(%s / ?)만 드라이버에 맞춰 바꾼다.
"""
from contextlib import contextmanager
from urllib.parse import urlparse

import pandas as pd

from . import config
from .records import COLUMNS

FIT_COLUMNS = [
    "ym", "region", "n", "gini",
    "ln_mu", "ln_sigma", "ln_ks",
    "pl_alpha", "pl_alpha_se", "pl_xmin", "pl_n_tail", "pl_tail_frac", "pl_ks",
    "lr_r", "lr_p", "ols_alpha", "updated_at",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    deal_id TEXT PRIMARY KEY,
    deal_date DATE NOT NULL,
    sgg_cd TEXT NOT NULL,
    umd_nm TEXT, jibun TEXT, apt_nm TEXT, apt_seq TEXT,
    area_m2 REAL NOT NULL, floor INTEGER, build_year INTEGER,
    price INTEGER NOT NULL,             -- 만원
    price_per_pyeong REAL NOT NULL,     -- 만원/평
    cdeal_type TEXT, cdeal_day TEXT,    -- 계약 해제 여부·일자
    source TEXT NOT NULL,               -- api / legacy
    loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_deals_date ON deals (deal_date);

CREATE TABLE IF NOT EXISTS fit_params (
    ym TEXT NOT NULL,                   -- 'YYYY-MM'
    region TEXT NOT NULL,               -- 11000 = 서울 전체, 그 외 자치구 코드
    n INTEGER, gini REAL,
    ln_mu REAL, ln_sigma REAL, ln_ks REAL,
    pl_alpha REAL, pl_alpha_se REAL, pl_xmin REAL,
    pl_n_tail INTEGER, pl_tail_frac REAL, pl_ks REAL,
    lr_r REAL, lr_p REAL,               -- R>0: power-law 우세, R<0: log-normal 우세
    ols_alpha REAL,                     -- 기존 방식(히스토그램 OLS) α, 비교용
    updated_at TEXT,
    PRIMARY KEY (ym, region)
);

CREATE TABLE IF NOT EXISTS collect_log (
    run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    lawd_cd TEXT, deal_ymd TEXT, n_rows INTEGER, n_cancelled INTEGER
);
"""


def _py(v):
    """numpy 값을 파이썬 기본형으로 바꾼다.

    numpy 2부터 repr이 'np.float64(0.26)' 형태라 psycopg2가 SQL에 그대로 넣어 오류가 난다.
    NaN은 NULL로 저장한다.
    """
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and v != v:
        return None
    return v


class Database:
    def __init__(self, url=None):
        self.url = url or config.DB_URL
        u = urlparse(self.url)
        self.kind = "sqlite" if u.scheme == "sqlite" else "postgres"
        self.ph = "?" if self.kind == "sqlite" else "%s"
        if self.kind == "sqlite":
            self._path = self.url.replace("sqlite:///", "", 1)
        else:
            self._pg = dict(host=u.hostname, port=u.port or 5432,
                            user=u.username, password=u.password,
                            dbname=u.path.lstrip("/"))
        self.init_schema()

    @contextmanager
    def connect(self):
        if self.kind == "sqlite":
            import sqlite3
            conn = sqlite3.connect(self._path)
        else:
            import psycopg2
            conn = psycopg2.connect(**self._pg)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self):
        with self.connect() as conn:
            cur = conn.cursor()
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    cur.execute(stmt)

    def _upsert(self, table, cols, keys, rows):
        if not rows:
            return 0
        sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in keys)
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join([self.ph] * len(cols))}) "
               f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {sets}")
        with self.connect() as conn:
            conn.cursor().executemany(
                sql, [tuple(_py(r.get(c)) for c in cols) for r in rows])
        return len(rows)

    def upsert_deals(self, records):
        """신규 거래는 추가, 이미 있는 거래는 해제 여부 등 최신 값으로 갱신."""
        return self._upsert("deals", COLUMNS, ["deal_id"], records)

    def upsert_fits(self, rows):
        return self._upsert("fit_params", FIT_COLUMNS, ["ym", "region"], rows)

    def log_collect(self, lawd_cd, deal_ymd, n_rows, n_cancelled):
        with self.connect() as conn:
            conn.cursor().execute(
                f"INSERT INTO collect_log (lawd_cd, deal_ymd, n_rows, n_cancelled) "
                f"VALUES ({self.ph}, {self.ph}, {self.ph}, {self.ph})",
                (lawd_cd, deal_ymd, n_rows, n_cancelled))

    def query(self, sql, params=()):
        """SQL 결과를 DataFrame으로. SQL 안의 자리표시자는 '?'로 쓴다."""
        if self.kind == "postgres":
            sql = sql.replace("?", "%s")
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)

    def month_prices(self, ym):
        """해당 월의 거래 (계약 해제 제외)."""
        start = f"{ym}-01"
        y, m = map(int, ym.split("-"))
        end = f"{y + (m == 12):04d}-{m % 12 + 1:02d}-01"
        return self.query(
            "SELECT sgg_cd, umd_nm, apt_nm, price_per_pyeong FROM deals "
            "WHERE deal_date >= ? AND deal_date < ? "
            "AND (cdeal_type IS NULL OR cdeal_type <> 'O')",
            (start, end))

    def month_complexes(self, ym):
        """해당 월의 단지별 평균 평당가격. 분석 단위는 거래가 아니라 단지.

        한 단지에서 여러 건(예: 일괄 매각 수백 건)이 거래돼도 단지 평균 하나로 들어간다.
        단지는 (자치구, 법정동, 단지명)으로 식별한다. 기존 2011~2019 데이터엔 단지 코드가 없어서.
        """
        df = self.month_prices(ym)
        if df.empty:
            return df.assign(n_deals=[])
        return (df.groupby(["sgg_cd", "umd_nm", "apt_nm"], as_index=False)
                  .agg(price_per_pyeong=("price_per_pyeong", "mean"),
                       n_deals=("price_per_pyeong", "size")))
