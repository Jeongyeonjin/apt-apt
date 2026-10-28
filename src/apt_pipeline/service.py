"""API가 돌려줄 값을 만드는 조회 함수 (FastAPI 없이도 테스트 가능)."""
import math

from . import config
from .db import Database

NAMES = {config.SEOUL: "서울 전체", **config.SEOUL_GU}
CODES = {v: k for k, v in NAMES.items()} | {"서울": config.SEOUL}
FIELDS = {"n": "n", "gini": "gini", "sigma": "ln_sigma", "mu": "ln_mu",
          "alpha": "ols_alpha", "alpha_mle": "pl_alpha", "tail_start": "pl_xmin",
          "lr_r": "lr_r", "lr_p": "lr_p"}


class NotFound(LookupError):
    pass


def region_code(region):
    """'강남구', '서울', '11680' 모두 받는다."""
    if region in NAMES:
        return region
    if region in CODES:
        return CODES[region]
    raise NotFound(f"지역을 찾을 수 없음: {region}")


def _clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 4) if isinstance(v, float) else v


def _row(r):
    out = {"ym": r["ym"], "region": NAMES[r["region"]], "region_code": r["region"]}
    out.update({k: _clean(r.get(col)) for k, col in FIELDS.items()})
    out["n"] = int(out["n"]) if out["n"] is not None else None
    # 몸통 log-normal의 중앙값 exp(μ) = 대표 평당가격(만원)
    # (몸통 피팅이 발산한 달은 μ가 비정상적으로 커서 계산하지 않는다)
    mu = out["mu"]
    out["median_price"] = round(math.exp(mu)) if mu is not None and mu < 20 else None
    return out


def regions():
    return [{"code": c, "name": n} for c, n in NAMES.items()]


def fit(region, ym, db=None):
    code = region_code(region)
    df = (db or Database()).query("SELECT * FROM fit_params WHERE region = ? AND ym = ?", (code, ym))
    if df.empty:
        raise NotFound(f"{NAMES[code]} {ym} 피팅 결과 없음")
    return _row(df.iloc[0].to_dict())


def trend(region, start=None, end=None, db=None):
    code = region_code(region)
    sql, params = "SELECT * FROM fit_params WHERE region = ?", [code]
    if start:
        sql += " AND ym >= ?"; params.append(start)
    if end:
        sql += " AND ym <= ?"; params.append(end)
    df = (db or Database()).query(sql + " ORDER BY ym", tuple(params))
    if df.empty:
        raise NotFound(f"{NAMES[code]} 피팅 결과 없음")
    return [_row(r) for r in df.to_dict("records")]


def months(region, db=None):
    code = region_code(region)
    df = (db or Database()).query("SELECT ym FROM fit_params WHERE region = ? ORDER BY ym", (code,))
    if df.empty:
        raise NotFound(f"{NAMES[code]} 피팅 결과 없음")
    return df["ym"].tolist()


def latest(db=None):
    df = (db or Database()).query("SELECT MAX(ym) AS ym FROM fit_params WHERE region = ?", (config.SEOUL,))
    ym = df["ym"].iloc[0]
    if ym is None:
        raise NotFound("피팅 결과 없음")
    return fit(config.SEOUL, ym, db)
