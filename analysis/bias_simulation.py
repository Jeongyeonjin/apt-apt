"""꼬리 지수 α 추정 방법 비교 시뮬레이션.

정답 α를 아는 power-law 표본을 만들어, 파이프라인이 쓰는 두 방법이 α를 얼마나 정확히 찾는지 본다.
  - OLS : 상위 5% 꼬리를 선형 60구간 히스토그램으로 만들고 구간 가운데 밀도에 c·x^(-α)를 최소제곱
          (기존 연구 코드 방식, 파이프라인의 기준값 ols_alpha)
  - MLE : 같은 꼬리에 최대우도추정 α = 1 + n / Σ ln(x/x_min) (비교값 pl_alpha)

실행: PYTHONPATH=src python analysis/bias_simulation.py
"""
import numpy as np

from apt_pipeline import config
from apt_pipeline.fitting import mle_powerlaw, ols_powerlaw, tail_threshold, tail_upper

REPEAT = 300
SEED = 0


def one_run(rng, alpha, n):
    x = (rng.pareto(alpha - 1, n) + 1) * 1000          # pdf ∝ x^(-α)
    xmin = tail_threshold(x)
    tail = x[x >= xmin]
    ols = ols_powerlaw(tail, xmax=tail_upper(x))
    mle, _, _ = mle_powerlaw(tail, xmin)
    return (ols[0] if ols else np.nan), mle


def main():
    rng = np.random.default_rng(SEED)
    print(f"꼬리 = 상위 {100 - config.TAIL_PCT:g}%, OLS 구간 {config.TAIL_BINS}개, 반복 {REPEAT}회\n")
    print("| 실제 α | 전체 표본 | 꼬리 표본 | OLS 평균 (표준편차) | MLE 평균 (표준편차) |")
    print("|---|---|---|---|---|")
    for n in (1000, 5000):
        for alpha in (2.5, 3.0, 4.0):
            r = np.array([one_run(rng, alpha, n) for _ in range(REPEAT)])
            o, m = r[:, 0], r[:, 1]
            print(f"| {alpha} | {n:,} | {round(n * (100 - config.TAIL_PCT) / 100)} | "
                  f"{np.nanmean(o):.2f} ({np.nanstd(o):.2f}) | {m.mean():.2f} ({m.std():.2f}) |")


if __name__ == "__main__":
    main()
