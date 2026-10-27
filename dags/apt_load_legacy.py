"""(수동 실행 1회) 기존 2011~2019 CSV 적재 → 월별 피팅.

legacy_data/ 폴더에 예전 CSV들을 넣고 Airflow 화면에서 이 DAG를 Trigger 하면 된다.
"""
import pendulum
from airflow.sdk import dag, task

from apt_pipeline import jobs
from apt_pipeline.db import Database
from apt_pipeline.legacy_loader import load_folder

LEGACY_DIR = "/opt/airflow/legacy_data"


@dag(
    dag_id="apt_load_legacy",
    schedule=None,                          # 수동 실행 전용
    start_date=pendulum.datetime(2026, 10, 1, tz="Asia/Seoul"),
    catchup=False,
    tags=["apt", "legacy"],
)
def apt_load_legacy():

    @task
    def load():
        return load_folder(LEGACY_DIR, Database())   # 적재된 'YYYY-MM' 목록

    @task(max_active_tis_per_dag=8)
    def fit(ym):
        return jobs.refit(ym)

    fit.expand(ym=load())


apt_load_legacy()
