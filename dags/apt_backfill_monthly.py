"""2024-01 ~ 2026-08 과거 데이터 백필 (2026-09부터는 매일 수집 DAG가 담당).

월 단위 스케줄 + catchup=True 로 켜두면 Airflow가 지난 달들을 하나씩 실행한다.
max_active_runs=1 이라 한 번에 한 달씩, 순서대로 진행된다.
(서울 25구 × 32개월 ≈ 800회 호출 → 하루 한도 10,000회 안에서 충분)
"""
from datetime import timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task

from apt_pipeline import jobs

KST = pendulum.timezone("Asia/Seoul")


@dag(
    dag_id="apt_backfill_monthly",
    schedule="@monthly",
    start_date=pendulum.datetime(2024, 1, 1, tz=KST),
    end_date=pendulum.datetime(2026, 8, 31, tz=KST),
    catchup=True,
    max_active_runs=1,
    default_args={"retries": 3, "retry_delay": timedelta(minutes=5)},
    tags=["apt", "backfill"],
)
def apt_backfill_monthly():

    @task
    def target_month():
        # logical_date = 매월 1일 00:00(KST) → 그 달을 담당
        start = get_current_context()["logical_date"].in_timezone(KST)
        return start.format("YYYY-MM")

    @task
    def plan(ym):
        return jobs.plan_jobs([ym])

    @task(max_active_tis_per_dag=4)
    def collect(job):
        return jobs.collect(job["lawd_cd"], job["ym"])

    @task
    def check(results, ym):
        return jobs.quality_check(list(results), required_months=[ym])

    @task
    def fit(ym):
        return jobs.refit(ym)

    ym = target_month()
    results = collect.expand(job=plan(ym))
    check(results, ym) >> fit(ym)


apt_backfill_monthly()
