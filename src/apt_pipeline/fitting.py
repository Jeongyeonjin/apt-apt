"""단지별 월평균 평당가격 분포 피팅: 몸통과 꼬리를 나눠서 맞춘다.

분석 단위는 단지(한 달 동안 거래된 단지마다 평균 평당가격 1개).

- 경계 x_min = 95번째 백분위수 (상위 5%가 꼬리)
- 몸통 (x < x_min): log-normal, MLE. 위쪽이 x_min에서 잘린 분포라 '잘린 log-normal'로 추정
- 꼬리 (x ≥ x_min): power-law
    * ols_alpha (기준값): 기존 연구 코드 방식. 꼬리 데이터만으로 선형 60구간 히스토그램(밀도)을
      만들고, 구간 가운데 점들에 c·x^(-α)를 최소제곱(curve_fit)으로 맞춤.
      상위 0.05%(극단값) 구간은 피팅에서 제외
    * pl_alpha  (비교용): 최대우도추정(MLE). OLS의 편향을 확인하는 용도
- 꼬리 비교 검정: 꼬리에서 power-law vs 잘린 log-normal 로그우도비 R, Vuong p값
- 지니계수 (전체)
"""
import warnings
from datetime import datetime, timezone

import numpy as np
from scipy import optimize, special, stats

from . import config


def gini(x):
    x = np.sort(np.asarray(x, float))
    n = len(x)
    return float((2 * np.arange(1, n + 1) - n - 1) @ x / (n * x.sum()))


def tail_threshold(x):
    return float(np.percentile(x, config.TAIL_PCT))


def _minimize(f, start):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return optimize.minimize(f, start, method="Nelder-Mead",
                                 options={"maxiter": 4000, "xatol": 1e-7, "fatol": 1e-7}).x


# ---------- 몸통: 위쪽이 잘린 log-normal (MLE) ----------
def fit_body_lognormal(body, xmin):
    """x < xmin 구간만 관측된 log-normal의 μ, σ를 MLE로 추정."""
    lx = np.log(body)
    lmin = np.log(xmin)

    def nll(p):
        mu, s = p[0], np.exp(p[1])
        return -(stats.norm.logpdf(lx, mu, s) - stats.norm.logcdf(lmin, mu, s)).sum()

    mu, log_s = _minimize(nll, (lx.mean(), np.log(lx.std())))
    s = float(np.exp(log_s))
    # KS: 몸통 경험분포 vs 잘린 log-normal 분포
    cdf = lambda v: stats.norm.cdf(np.log(v), mu, s) / stats.norm.cdf(lmin, mu, s)
    ks = stats.kstest(body, cdf).statistic
    return float(mu), s, float(ks)


def body_pdf(x, mu, sigma, xmin):
    """대시보드용: 잘린 log-normal 밀도."""
    x = np.asarray(x, float)
    return stats.lognorm.pdf(x, sigma, scale=np.exp(mu)) / stats.norm.cdf(np.log(xmin), mu, sigma)


# ---------- 꼬리: power-law ----------
def histogram_mids(x, bins):
    """선형(같은 폭) 구간 히스토그램. 반환: (구간 가운데, 밀도). 밀도는 x 안에서 합이 1."""
    dens, edges = np.histogram(np.asarray(x, float), bins, density=True)
    return (edges[:-1] + edges[1:]) / 2, dens


def ols_powerlaw(tail, xmax=None, bins=None):
    """기존 연구 코드 방식의 꼬리 α.

    상위 5%로 먼저 자른 꼬리 데이터만으로 선형 구간 히스토그램을 만들고,
    구간 가운데 밀도에 c·x^(-α)를 curve_fit(최소제곱)으로 맞춘다.
    (기존 코드는 구간 왼쪽 끝을 썼고, 여기서는 가운데를 쓴다)
    xmax가 주어지면 구간 가운데가 xmax보다 큰 구간(극단값, 표본이 너무 적은 끝부분)은 피팅에서 뺀다.
    히스토그램 자체는 꼬리 전체로 만들어서 구간 폭은 그대로다.
    수치 안정을 위해 x를 꼬리 최솟값으로 나눠 맞춘 뒤 c를 원래 단위로 되돌린다.
    반환: (α, c, 구간 가운데, 밀도, 피팅에 쓴 구간 여부). 실패하면 None.
    """
    tail = np.asarray(tail, float)
    if len(tail) < 10:
        return None
    mids, dens = histogram_mids(tail, bins or config.TAIL_BINS)
    used = mids <= xmax if xmax is not None else np.ones(len(mids), bool)
    if used.sum() < 3:
        return None
    x0 = tail.min()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            (a, c), _ = optimize.curve_fit(lambda v, a, c: c * (v / x0) ** (-a),
                                           mids[used], dens[used],
                                           p0=(3.0, dens[0]), maxfev=50000)
    except (RuntimeError, ValueError):
        return None
    return float(a), float(c * x0 ** a), mids, dens, used


def tail_upper(x):
    """꼬리 피팅 상한: 이 값보다 큰 극단값 구간은 피팅에서 제외 (기본 99.95번째 백분위수)."""
    return float(np.percentile(x, config.TAIL_UPPER_PCT))


def mle_powerlaw(tail, xmin):
    a = 1 + len(tail) / np.log(tail / xmin).sum()
    t = np.sort(tail)
    ks = np.abs(np.arange(len(t)) / len(t) - (1 - (t / xmin) ** (1 - a))).max()
    return float(a), float((a - 1) / np.sqrt(len(t))), float(ks)


def fit_tail_lognormal(tail, xmin):
    """꼬리 구간의 잘린 log-normal(아래쪽이 xmin에서 잘림) MLE. 비교 검정용."""
    lt = np.log(tail)
    lmin = np.log(xmin)

    def ll(p):
        mu, s = p[0], np.exp(p[1])
        return stats.norm.logpdf(lt, mu, s) - lt - stats.norm.logsf(lmin, mu, s)

    p = _minimize(lambda q: -ll(q).sum(), (lt.mean(), np.log(max(lt.std(), 1e-3))))
    return p, ll(p)


def compare_tail(tail, xmin, alpha):
    """꼬리에서 power-law(MLE) vs 잘린 log-normal 로그우도비 R, Vuong p값.

    R > 0 이면 power-law, R < 0 이면 log-normal이 더 잘 맞음. p < 0.1일 때만 판정.
    """
    l_pl = np.log(alpha - 1) - np.log(xmin) - alpha * np.log(tail / xmin)
    _, l_ln = fit_tail_lognormal(tail, xmin)
    d = l_pl - l_ln
    R, sd = d.sum(), d.std()
    p = float(special.erfc(abs(R) / (np.sqrt(2 * len(tail)) * sd))) if sd > 0 else 1.0
    return float(R), p


# ---------- 한 그룹 피팅 ----------
def fit_sample(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x) & (x > 0)]
    row = dict(n=int(len(x)))
    if len(x) < config.MIN_SAMPLE:
        return row
    xmin = tail_threshold(x)
    body, tail = x[x < xmin], x[x >= xmin]
    row["gini"] = gini(x)
    row["pl_xmin"] = xmin
    row["pl_n_tail"] = int(len(tail))
    row["pl_tail_frac"] = len(tail) / len(x)
    row["ln_mu"], row["ln_sigma"], row["ln_ks"] = fit_body_lognormal(body, xmin)
    if len(tail) >= config.MIN_TAIL:
        ols = ols_powerlaw(tail, xmax=tail_upper(x))
        row["ols_alpha"] = ols[0] if ols else None
        row["pl_alpha"], row["pl_alpha_se"], row["pl_ks"] = mle_powerlaw(tail, xmin)
        row["lr_r"], row["lr_p"] = compare_tail(tail, xmin, row["pl_alpha"])
    return row


def fit_month(db, ym):
    """한 달치 단지 평균을 서울 전체 + 자치구별로 피팅해 fit_params에 저장. n = 단지 수."""
    df = db.month_complexes(ym)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    groups = [(config.SEOUL, df)] + [(g, d) for g, d in df.groupby("sgg_cd")]
    rows = []
    for region, d in groups:
        row = fit_sample(d["price_per_pyeong"].to_numpy())
        rows.append(dict(row, ym=ym, region=str(region), updated_at=now))
    db.upsert_fits(rows)
    return rows
