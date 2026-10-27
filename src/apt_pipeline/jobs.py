"""DAG 태스크가 호출하는 작업 단위. Airflow 없이도 그대로 실행·테스트할 수 있다."""
from . import api, config
from .db import Database, _py
from .fitting import fit_month

# 평당가격(만원/평)이 이 범위를 벗어나면 입력 오류로 의심
PRICE_RANGE = (100, 50_000)


def month_add(ym, k):
    """'YYYY-MM'에 k개월을 더한다."""
    y, m = map(int, ym.split("-"))
    t = y * 12 + (m - 1) + k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def plan_jobs(months):
    """(자치구 × 월) 조합 목록. Airflow에서 이 목록으로 태스크를 동적 생성한다."""
    return [{"lawd_cd": c, "ym": ym} for ym in months for c in config.SEOUL_GU]


def collect(lawd_cd, ym, db=None, fetch=api.fetch_month):
    db = db or Database()
    recs = fetch(lawd_cd, ym.replace("-", ""))
    db.upsert_deals(recs)
    n_cancel = sum(r["cdeal_type"] == "O" for r in recs)
    db.log_collect(lawd_cd, ym, len(recs), n_cancel)
    return {"lawd_cd": lawd_cd, "ym": ym, "n": len(recs), "n_cancelled": n_cancel}


def quality_check(results, required_months, db=None):
    """수집 결과 점검. 실패하면 예외 → 이후 피팅 태스크가 실행되지 않는다.

    required_months: 거래가 반드시 있어야 하는 달 (진행 중인 이번 달은 빼고 넘긴다)
    """
    db = db or Database()
    problems = []
    by_month = {}
    for r in results:
        by_month[r["ym"]] = by_month.get(r["ym"], 0) + r["n"]
    for ym in required_months:
        if by_month.get(ym, 0) == 0:
            problems.append(f"{ym}: 서울 전체 거래 0건 (API 키·주소 확인 필요)")
    for ym in by_month:
        df = db.month_prices(ym)
        if df.empty:
            continue
        p = df["price_per_pyeong"]
        bad = ((p < PRICE_RANGE[0]) | (p > PRICE_RANGE[1])).mean()
        if bad > 0.01:
            problems.append(f"{ym}: 평당가격 범위 밖 거래 비율 {bad:.1%}")
    if problems:
        raise ValueError("데이터 품질 점검 실패\n" + "\n".join(problems))
    return by_month


def refit(ym, db=None):
    rows = fit_month(db or Database(), ym)
    seoul = next(r for r in rows if r["region"] == config.SEOUL)
    return {k: _py(seoul.get(k)) for k in ("ym", "n", "ols_alpha", "pl_alpha", "ln_sigma")}


def all_months(db=None):
    """deals에 있는 모든 'YYYY-MM' (피팅 방법을 바꿨을 때 전체 재계산용)."""
    df = (db or Database()).query("SELECT DISTINCT deal_date FROM deals")
    return sorted({str(d)[:7] for d in df["deal_date"]})
