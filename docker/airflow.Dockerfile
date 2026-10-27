FROM apache/airflow:3.3.2
# pandas, numpy, requests, psycopg2는 이미지에 포함. 피팅에 필요한 scipy만 추가
RUN pip install --no-cache-dir scipy
