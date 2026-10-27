"""API 응답과 기존 CSV를 같은 형태의 거래 레코드로 맞추는 공통 함수."""
import hashlib
from collections import Counter

from .config import PYEONG

COLUMNS = [
    "deal_id", "deal_date", "sgg_cd", "umd_nm", "jibun", "apt_nm", "apt_seq",
    "area_m2", "floor", "build_year", "price", "price_per_pyeong",
    "cdeal_type", "cdeal_day", "source",
]


def to_int(v):
    v = (v or "").replace(",", "").strip()
    try:
        return int(float(v))
    except ValueError:
        return None


def to_float(v):
    v = (v or "").replace(",", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def make_record(*, year, month, day, sgg_cd, umd_nm, jibun, apt_nm, apt_seq,
                area_m2, floor, build_year, price, cdeal_type, cdeal_day, source):
    """정제된 값으로 레코드 dict를 만든다. 가격·면적·날짜가 없으면 None."""
    if not (year and month and day and price and area_m2) or area_m2 <= 0:
        return None
    return {
        "deal_date": f"{year:04d}-{month:02d}-{day:02d}",
        "sgg_cd": sgg_cd, "umd_nm": umd_nm, "jibun": jibun,
        "apt_nm": apt_nm, "apt_seq": apt_seq,
        "area_m2": area_m2, "floor": floor, "build_year": build_year,
        "price": price,                                   # 만원
        "price_per_pyeong": round(price / (area_m2 / PYEONG), 2),  # 만원/평
        "cdeal_type": cdeal_type or None,                 # 'O' = 계약 해제
        "cdeal_day": cdeal_day or None,
        "source": source,
    }


def assign_ids(records):
    """거래 고유키(deal_id)를 붙인다.

    API에는 거래 고유번호가 없어서, 거래를 식별하는 값들을 해시한다.
    같은 날 같은 단지·면적·층·가격 거래가 실제로 2건 이상일 수 있으므로
    같은 해시가 반복되면 순번(#0, #1, ...)을 붙여 둘 다 살린다.
    해제 여부는 키에 넣지 않는다 → 나중에 해제되면 같은 행이 갱신된다.
    """
    seen = Counter()
    out = []
    for r in records:
        if r is None:
            continue
        key = "|".join(str(r[k]) for k in
                       ("deal_date", "sgg_cd", "umd_nm", "jibun", "apt_nm",
                        "area_m2", "floor", "price"))
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        r = dict(r, deal_id=f"{h}#{seen[h]}")
        seen[h] += 1
        out.append(r)
    return out
