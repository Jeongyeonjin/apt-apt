"""서울 아파트 분포 지표 API.

실행: uvicorn main:app --host 0.0.0.0 --port 8000  (Docker Compose의 api 서비스)
문서: http://localhost:8000/docs
"""
from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import RedirectResponse

from apt_pipeline import service
from apt_pipeline.db import Database

app = FastAPI(
    title="서울 아파트 평당가격 분포 API",
    description="단지별 월평균 평당가격 분포의 피팅 결과(몸통 Log-normal, 꼬리 Power-law, 지니계수)를 조회합니다.",
    version="1.0.0",
)
db = Database()


def _call(fn, *args, **kw):
    try:
        return fn(*args, db=db, **kw)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


def _ex(*values):
    """Swagger 문서에서 'Try it out'을 누르면 미리 채워질 예시값"""
    return {v: {"value": v} for v in values}


REGION = Path(description="'서울', '강남구' 또는 법정동 코드 5자리", openapi_examples={"서울": {"value": "서울"}, "강남구": {"value": "강남구"},
                                "코드 (강남구)": {"value": "11680"}})
YM = Path(description="YYYY-MM", openapi_examples=_ex("2026-10", "2025-12", "2024-01"))


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/health", summary="서버 상태")
def health():
    return {"status": "ok"}


@app.get("/regions", summary="조회 가능한 지역 목록")
def regions():
    return service.regions()


@app.get("/latest", summary="서울 전체 최신 월 지표")
def latest():
    return _call(service.latest)


@app.get("/fit/{region}/{ym}", summary="지역·월별 분포 지표")
def fit(region: str = REGION, ym: str = YM):
    """구별 꼬리(상위 5%)는 단지 수가 30개 미만이면 α를 계산하지 않아 null로 나온다."""
    return _call(service.fit, region, ym)


@app.get("/trend/{region}", summary="지역별 월별 지표 추이")
def trend(region: str = REGION,
          start: str | None = Query(None, description="시작 월 YYYY-MM", openapi_examples=_ex("2024-01")),
          end: str | None = Query(None, description="끝 월 YYYY-MM", openapi_examples=_ex("2026-10"))):
    return _call(service.trend, region, start=start, end=end)
