"""기존 2011~2019 CSV(옛 API로 수집, 헤더 없음, ms949)를 deals 테이블로 불러온다.

사용: python -m apt_pipeline.legacy_loader <CSV 폴더>
"""
import sys
from pathlib import Path

import pandas as pd

from .records import assign_ids, make_record, to_float, to_int

# 기존 수집 코드가 header=False로 저장한 21개 컬럼 순서
LEGACY_COLUMNS = [
    "거래금액", "건축년도", "년", "도로명", "도로명건물본번호코드", "도로명건물부번호코드",
    "도로명시군구번호코드", "도로명일련번호코드", "도로명지상지하코드", "도로명코드",
    "법정동", "법정동본번코드", "법정동부번코드", "법정동시군구코드", "법정동읍면동코드",
    "법정동지번코드", "아파트", "월", "일", "일련번호", "전용면적",
]


def _jibun(main, sub):
    m, s = to_int(main), to_int(sub)
    if m is None:
        return ""
    return f"{m}-{s}" if s else str(m)


def read_legacy_csv(path):
    for enc in ("cp949", "utf-8-sig"):       # 기존 파일은 ms949, 혹시 UTF-8이면 재시도
        try:
            df = pd.read_csv(path, header=None, names=LEGACY_COLUMNS, dtype=str,
                             encoding=enc, keep_default_na=False)
            break
        except UnicodeDecodeError:
            if enc == "utf-8-sig":
                raise
    df = df[df["법정동시군구코드"].str.strip().str.startswith("11")]  # 서울만
    recs = [
        make_record(
            year=to_int(r["년"]), month=to_int(r["월"]), day=to_int(r["일"]),
            sgg_cd=r["법정동시군구코드"].strip(), umd_nm=r["법정동"].strip(),
            jibun=_jibun(r["법정동본번코드"], r["법정동부번코드"]),
            apt_nm=r["아파트"].strip(), apt_seq="",
            area_m2=to_float(r["전용면적"]), floor=None,   # 기존 데이터엔 층 없음
            build_year=to_int(r["건축년도"]), price=to_int(r["거래금액"]),
            cdeal_type=None, cdeal_day=None, source="legacy",
        )
        for r in df.to_dict("records")
    ]
    ok = [r for r in recs if r is not None and _plausible(r)]
    return assign_ids(ok), len(df) - len(ok)


def _plausible(r):
    """형식이 다른 CSV가 섞여도 엉뚱한 값이 들어가지 않도록 값 범위를 검사."""
    y, m, d = map(int, r["deal_date"].split("-"))
    return (2006 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31
            and len(r["sgg_cd"]) == 5 and 5 <= r["area_m2"] <= 400
            and 100 <= r["price_per_pyeong"] <= 50_000)


def load_folder(folder, db):
    """폴더 안 CSV를 전부 적재하고, 적재된 연월 목록을 돌려준다."""
    files = sorted(Path(folder).rglob("*.csv"))
    if not files:
        raise FileNotFoundError(f"{folder} 에 CSV가 없습니다.")
    months, total, dropped = set(), 0, 0
    for f in files:
        try:
            recs, bad = read_legacy_csv(f)
        except Exception as e:                       # 인코딩·형식이 전혀 다른 파일
            print(f"건너뜀 {f.name}: {e}")
            continue
        if bad > len(recs):                          # 절반 이상 깨지면 다른 형식의 파일
            print(f"건너뜀 {f.name}: 원본 거래 파일 형식이 아님 (정상 {len(recs)}, 오류 {bad})")
            continue
        db.upsert_deals(recs)
        total += len(recs)
        dropped += bad
        months.update(r["deal_date"][:7] for r in recs)
    print(f"CSV {len(files)}개, 적재 {total:,}건, 형식 오류로 제외 {dropped:,}건")
    return sorted(months)


if __name__ == "__main__":
    from .db import Database
    load_folder(sys.argv[1], Database())
