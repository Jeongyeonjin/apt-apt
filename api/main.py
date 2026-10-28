"""서울 아파트 분포 지표 API.

실행: uvicorn main:app --host 0.0.0.0 --port 8000  (Docker Compose의 api 서비스)
조회 화면: http://localhost:8000   API 문서: http://localhost:8000/docs
"""
from enum import Enum
from pathlib import Path as FilePath

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from apt_pipeline import config, service
from apt_pipeline.db import Database

app = FastAPI(
    title="서울 아파트 평당가격 분포 API",
    description=("단지별 월평균 평당가격 분포의 피팅 결과(하위 95% Log-normal, 상위 5% Power-law, 지니계수)를 조회합니다.\n\n"
                 "조회 화면은 [http://localhost:8000](/) 에 있습니다."),
    version="1.1.0",
)
db = Database()
STATIC = FilePath(__file__).parent / "static"

# 드롭다운으로 고를 수 있도록 지역을 목록(Enum)으로 정의
Region = Enum("Region", [(n, n) for n in ["서울", *config.SEOUL_GU.values()]], type=str)
YM = Path(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="연월 YYYY-MM (예: 2026-10)",
          json_schema_extra={"example": "2026-10"})


class Fit(BaseModel):
    ym: str = Field(description="연월 YYYY-MM")
    region: str = Field(description="지역 이름")
    region_code: str = Field(description="법정동 코드 5자리")
    n: int | None = Field(description="분석한 단지 수 (단지별 월평균 평당가격 사용)")
    median_price: int | None = Field(description="대표 평당가격, 만원 (하위 95% log-normal의 중앙값 exp(μ))")
    tail_start: float | None = Field(description="상위 5%가 시작되는 평당가격, 만원")
    sigma: float | None = Field(description="가격 퍼짐: 하위 95% log-normal의 σ. 클수록 단지 간 가격 차이가 큼")
    mu: float | None = Field(description="하위 95% log-normal의 μ")
    gini: float | None = Field(description="지니계수. 0이면 모든 단지 가격이 같고 1에 가까울수록 불평등")
    alpha: float | None = Field(description="꼬리 지수: 상위 5% power-law 기울기(히스토그램 OLS). 작을수록 초고가 단지가 많음")
    alpha_mle: float | None = Field(description="같은 꼬리의 MLE 지수 (비교용)")
    lr_r: float | None = Field(description="꼬리 비교 로그우도비. 양수면 power-law, 음수면 log-normal 쪽")
    lr_p: float | None = Field(description="로그우도비 검정 p값")


def _call(fn, *args, **kw):
    try:
        return fn(*args, db=db, **kw)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/health", summary="서버 상태")
def health():
    return {"status": "ok"}


@app.get("/regions", summary="조회 가능한 지역 목록")
def regions():
    return service.regions()


@app.get("/months/{region}", summary="지역별 조회 가능한 월 목록", response_model=list[str])
def months(region: Region):
    return _call(service.months, region.value)


@app.get("/complexes/{region}/{ym}", summary="피팅에 쓴 단지별 평균 평당가격 (원자료)")
def complexes(region: Region, ym: str = YM):
    """그달 거래된 단지마다 평균 평당가격(만원)과 거래 건수. 싼 순서."""
    return _call(service.complexes, region.value, ym)


@app.get("/latest", summary="서울 전체 최신 월 지표", response_model=Fit)
def latest():
    return _call(service.latest)


@app.get("/fit/{region}/{ym}", summary="지역·월별 분포 지표", response_model=Fit)
def fit(region: Region, ym: str = YM):
    """그달 단지가 30곳 미만이면 분포 지표는 null, 상위 5% 단지가 30곳 미만이면 꼬리 지수(alpha)는 null로 나옵니다."""
    return _call(service.fit, region.value, ym)


@app.get("/trend/{region}", summary="지역별 월별 지표 추이", response_model=list[Fit])
def trend(region: Region,
          start: str | None = Query(None, description="시작 월 YYYY-MM (비우면 처음부터)"),
          end: str | None = Query(None, description="끝 월 YYYY-MM (비우면 최신까지)")):
    return _call(service.trend, region.value, start=start, end=end)
