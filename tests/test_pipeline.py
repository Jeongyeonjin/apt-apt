"""API·Airflow 없이 핵심 로직 검증 (SQLite + 가짜 응답).

실행: PYTHONPATH=src python tests/test_pipeline.py   (pytest로도 실행 가능)
"""
import csv
import os
import tempfile

import numpy as np

from apt_pipeline import config, jobs
from apt_pipeline.api import ApiError, parse_items
from apt_pipeline.db import Database
from apt_pipeline.fitting import fit_sample, histogram_mids, ols_powerlaw
from apt_pipeline.legacy_loader import LEGACY_COLUMNS, read_legacy_csv
from apt_pipeline.records import assign_ids


def item(amount, day, floor="5", cdeal="", area="84.97", apt="테스트아파트"):
    return f"""<item><aptDong></aptDong><aptNm>{apt}</aptNm><aptSeq>11680-1</aptSeq>
<buildYear>2005</buildYear><cdealDay>{'25.02.01' if cdeal else ''}</cdealDay>
<cdealType>{cdeal}</cdealType><dealAmount>{amount}</dealAmount><dealDay>{day}</dealDay>
<dealMonth>1</dealMonth><dealYear>2025</dealYear><excluUseAr>{area}</excluUseAr>
<floor>{floor}</floor><jibun>123</jibun><sggCd>11680</sggCd><umdNm>대치동</umdNm></item>"""


def response(items, total=None):
    total = len(items) if total is None else total
    return (f"<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header>"
            f"<body><items>{''.join(items)}</items><numOfRows>1000</numOfRows>"
            f"<pageNo>1</pageNo><totalCount>{total}</totalCount></body></response>")


def temp_db():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    return Database(f"sqlite:///{path}")


def test_parse_items_and_price_per_pyeong():
    recs, total, n = parse_items(response([item("250,000", "3"), item("190,000", "10", floor="")]))
    assert total == 2 and n == 2 and len(recs) == 2
    r = recs[0]
    assert r["price"] == 250000 and r["deal_date"] == "2025-01-03"
    assert abs(r["price_per_pyeong"] - 250000 / (84.97 / config.PYEONG)) < 0.01
    assert recs[1]["floor"] is None          # 빈 필드가 있어도 칸이 밀리지 않음


def test_api_error_raises():
    bad = ("<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR"
           "</returnAuthMsg><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")
    try:
        parse_items(bad)
    except ApiError as e:
        assert "30" in str(e)
    else:
        raise AssertionError("오류 응답인데 예외가 안 남")


def test_duplicate_deals_kept():
    recs, _, _ = parse_items(response([item("100,000", "5")] * 2))
    ids = [r["deal_id"] for r in assign_ids(recs)]
    assert len(set(ids)) == 2                 # 똑같은 거래 2건도 둘 다 보존


def test_upsert_updates_cancellation():
    db = temp_db()
    first = assign_ids(parse_items(response([item("100,000", "5"), item("120,000", "6")]))[0])
    db.upsert_deals(first)
    # 다음 날 재수집: 1건은 해제됨, 1건은 늦게 신고된 신규 거래
    second = assign_ids(parse_items(response(
        [item("100,000", "5", cdeal="O"), item("120,000", "6"), item("130,000", "7")]))[0])
    db.upsert_deals(second)
    df = db.query("SELECT price, cdeal_type FROM deals ORDER BY price")
    assert len(df) == 3                                     # 중복 없이 3건
    assert df.loc[df.price == 100000, "cdeal_type"].item() == "O"   # 해제 반영
    assert len(db.month_prices("2025-01")) == 2             # 해제 건은 피팅에서 제외


def test_fitting_recovers_alpha():
    rng = np.random.default_rng(0)
    x = (rng.pareto(2.0, 5000) + 1) * 2000                  # 실제 α = 3
    row = fit_sample(x)
    assert abs(row["pl_tail_frac"] - 0.05) < 0.005           # 꼬리 = 상위 5%
    assert abs(row["pl_alpha"] - 3.0) < 0.35                 # MLE (꼬리 250개)
    assert row["ols_alpha"] is not None                      # 기존 OLS 방식도 계산
    assert 0 < row["gini"] < 1


def test_lognormal_data_not_called_powerlaw():
    rng = np.random.default_rng(1)
    row = fit_sample(rng.lognormal(8, 0.5, 5000))
    # 몸통(하위 95%)만으로 잘린 log-normal을 맞춰도 원래 μ, σ를 복원해야 함
    assert abs(row["ln_mu"] - 8) < 0.03 and abs(row["ln_sigma"] - 0.5) < 0.03
    assert row["ln_ks"] < 0.03
    assert not (row["lr_r"] > 0 and row["lr_p"] < 0.1)      # power-law 우세로 판정하면 안 됨


def test_tail_histogram_midpoints_and_ols():
    rng = np.random.default_rng(4)
    tail = (rng.pareto(3.0, 2000) + 1) * 10000               # 꼬리만, 실제 α = 4
    mids, dens = histogram_mids(tail, 60)
    w = mids[1] - mids[0]
    assert np.isclose(mids[0], tail.min() + w / 2)           # 점 = 구간 가운데
    assert np.isclose((dens * w).sum(), 1.0)                 # 꼬리 안에서 밀도 합 1
    a, c, _, _, used = ols_powerlaw(tail)
    assert 3.0 < a < 4.5 and used.all()
    xmax = np.percentile(tail, 80)
    _, _, m, _, used = ols_powerlaw(tail, xmax=xmax)
    assert (m[used] <= xmax).all() and (m[~used] > xmax).all()   # 상한 넘는 구간만 제외


def test_small_sample_skips_fit():
    row = fit_sample(np.arange(1, 10, dtype=float))
    assert row == {"n": 9}


def test_legacy_csv_seoul_only():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "201904.csv")
    rows = []
    for sgg, amt in [("11680", "  95,000"), ("26110", "  30,000"), ("11110", " 깨진값")]:
        r = {c: "" for c in LEGACY_COLUMNS}
        r.update({"거래금액": amt, "건축년도": "2000", "년": "2019", "월": "4", "일": "12",
                  "법정동": " 대치동", "법정동본번코드": "0123", "법정동부번코드": "0000",
                  "법정동시군구코드": sgg, "아파트": "은마", "전용면적": "76.79"})
        rows.append([r[c] for c in LEGACY_COLUMNS])
    # 기존 코드의 pandas.to_csv처럼 쉼표 있는 값은 따옴표로 감싸서 저장
    with open(path, "w", encoding="cp949", newline="") as f:
        csv.writer(f).writerows(rows)
    recs, dropped = read_legacy_csv(path)
    assert len(recs) == 1 and dropped == 1                  # 부산 제외, 깨진 행 제외
    assert recs[0]["jibun"] == "123" and recs[0]["source"] == "legacy"


def test_end_to_end_collect_check_fit():
    db = temp_db()
    rng = np.random.default_rng(2)

    def fake_fetch(lawd_cd, deal_ymd):
        n = 120
        area = 84.97
        prices = rng.lognormal(np.log(3000), 0.45, n) * area / config.PYEONG
        items = [item(f"{int(p):,}", str(i % 28 + 1), apt=f"단지{i}")
                 .replace("<sggCd>11680", f"<sggCd>{lawd_cd}")
                 .replace("<dealMonth>1<", f"<dealMonth>{int(deal_ymd[4:])}<")
                 for i, p in enumerate(prices)]
        return assign_ids(parse_items(response(items))[0])

    months = ["2025-01", "2025-02"]
    results = [jobs.collect(j["lawd_cd"], j["ym"], db=db, fetch=fake_fetch)
               for j in jobs.plan_jobs(months)]
    assert len(results) == 50
    by_month = jobs.quality_check(results, required_months=months[:1], db=db)
    assert by_month["2025-01"] == 25 * 120
    out = jobs.refit("2025-01", db=db)
    assert out["n"] == 3000 and out["ln_sigma"] > 0.3          # 단지명이 모두 달라 단지 3000개
    fits = db.query("SELECT region FROM fit_params WHERE ym = ?", ("2025-01",))
    assert len(fits) == 26                                  # 서울 전체 + 25개 구


def test_quality_check_fails_on_empty_month():
    db = temp_db()
    try:
        jobs.quality_check([{"lawd_cd": "11110", "ym": "2025-01", "n": 0}],
                           required_months=["2025-01"], db=db)
    except ValueError as e:
        assert "0건" in str(e)
    else:
        raise AssertionError("0건인데 통과됨")


def test_numpy_values_become_python():
    from apt_pipeline.db import _py
    v = _py(np.float64(0.25))
    assert type(v) is float and repr(v) == "0.25"           # 'np.float64(0.25)'가 아님
    assert _py(np.int64(3)) == 3 and type(_py(np.int64(3))) is int
    assert _py(float("nan")) is None


def test_complex_average_is_unit():
    db = temp_db()
    bulk = [item("50,000", str(d % 28 + 1), area="16.48", apt="일괄단지") for d in range(30)]
    normal = [item(f"{100000 + i * 1000:,}", "5", apt=f"단지{i}") for i in range(10)]
    db.upsert_deals(assign_ids(parse_items(response(bulk + normal))[0]))
    c = db.month_complexes("2025-01")
    assert len(c) == 11                                        # 거래 40건 → 단지 11개
    assert c.loc[c.apt_nm == "일괄단지", "n_deals"].item() == 30


def test_service_queries():
    from apt_pipeline import service
    db = temp_db()
    db.upsert_fits([dict(ym=ym, region=r, n=100, gini=0.3, ln_mu=8.0, ln_sigma=0.45,
                         ols_alpha=3.2, pl_alpha=float("nan"), updated_at="t")
                    for ym in ("2026-09", "2026-10") for r in ("11000", "11680")])
    out = service.fit("강남구", "2026-10", db=db)
    assert out["region_code"] == "11680" and out["sigma"] == 0.45 and out["alpha_mle"] is None
    assert service.fit("11000", "2026-09", db=db)["region"] == "서울 전체"
    assert len(service.trend("서울", start="2026-10", db=db)) == 1
    assert service.latest(db=db)["ym"] == "2026-10"
    try:
        service.fit("없는구", "2026-10", db=db)
    except service.NotFound:
        pass
    else:
        raise AssertionError


def test_month_add():
    assert jobs.month_add("2025-01", -1) == "2024-12"
    assert jobs.month_add("2024-12", 1) == "2025-01"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("통과:", t.__name__)
    print(f"전체 {len(tests)}개 통과")
