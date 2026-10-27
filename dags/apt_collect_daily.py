"""매일 오전 6시: 최근 2개월 재수집 → 품질 점검 → 해당 월 피팅 갱신.

실거래가는 계약 후 30일 안에 신고하고, 나중에 계약이 해제되기도 한다.
그래서 '어제 거래'만 받으면 늦게 신고된 거래와 해제 정보를 놓친다.
매일 지난달과 이번 달을 통째로 다시 받아 upsert(있으면 갱신, 없으면 추가)한다.
"""
from datetime import timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task

from apt_pipeline import jobs

KST = pendulum.timezone("Asia/Seoul")

default_args = {
    "owner": "yeonjin",
    "retries": 3,                          # API 일시 오류는 자동 재시도
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
}


@dag(
    dag_id="apt_collect_daily",
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 10, 1, tz=KST),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["apt", "daily"],
)
def apt_collect_daily():

    @task
    def target_months():
        ctx = get_current_context()
        run = (ctx.get("data_interval_end") or pendulum.now(KST)).in_timezone(KST)
        this_month = run.format("YYYY-MM")
        return [jobs.month_add(this_month, -1), this_month]

    @task
    def plan(months):
        return jobs.plan_jobs(months)          # 25개 구 × 2개월 = 50개 작업

    # Dynamic Task Mapping: 작업 목록 길이만큼 태스크가 생기고,
    # max_active_tis_per_dag로 API 동시 호출 수를 제한한다.
    @task(max_active_tis_per_dag=4)
    def collect(job):
        return jobs.collect(job["lawd_cd"], job["ym"])

    @task
    def check(results, months):
        # 이번 달은 아직 신고가 덜 들어왔을 수 있으니 지난달만 '반드시 있어야 함'
        return jobs.quality_check(list(results), required_months=months[:1])

    @task
    def fit(ym):
        return jobs.refit(ym)

    months = target_months()
    results = collect.expand(job=plan(months))
    checked = check(results, months)
    fits = fit.expand(ym=months)
    checked >> fits


apt_collect_daily()
