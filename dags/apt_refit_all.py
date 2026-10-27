"""(수동 실행) DB에 있는 모든 월을 다시 피팅.

피팅 방법이나 설정(꼬리 비율 등)을 바꾼 뒤 한 번 Trigger 하면
fit_params 전체가 새 방법으로 다시 계산된다.
"""
import pendulum
from airflow.sdk import dag, task

from apt_pipeline import jobs


@dag(
    dag_id="apt_refit_all",
    schedule=None,
    start_date=pendulum.datetime(2026, 10, 1, tz="Asia/Seoul"),
    catchup=False,
    tags=["apt", "maintenance"],
)
def apt_refit_all():

    @task
    def months():
        return jobs.all_months()

    @task(max_active_tis_per_dag=8)
    def fit(ym):
        return jobs.refit(ym)

    fit.expand(ym=months())


apt_refit_all()
