-- Airflow 메타데이터 DB(airflow)와 분리된 분석용 DB(apt)
CREATE USER apt WITH PASSWORD 'apt';
CREATE DATABASE apt OWNER apt;
