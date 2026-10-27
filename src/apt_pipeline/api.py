"""공공데이터포털 '국토교통부_아파트 매매 실거래가 자료' API 수집."""
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import requests

from . import config
from .records import assign_ids, make_record, to_float, to_int


class ApiError(RuntimeError):
    pass


def _text(item, tag):
    el = item.find(tag)
    return el.text.strip() if el is not None and el.text else ""


def parse_items(xml_text):
    """응답 XML → (레코드 리스트, totalCount, 받은 item 수).

    기존 코드처럼 필드 개수로 칸을 맞추지 않고 태그 이름으로 꺼내므로
    빈 필드가 있어도 값이 옆 칸으로 밀리지 않는다.
    """
    root = ET.fromstring(xml_text)

    # 오류 응답: <resultCode>가 정상(00/000)이 아니거나, 게이트웨이 오류 형식
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
    msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or ""
    if code not in ("00", "000"):
        raise ApiError(f"API 오류 {code}: {msg}")

    total = to_int(root.findtext(".//totalCount")) or 0
    records, n_items = [], 0
    for it in root.iter("item"):
        n_items += 1
        records.append(make_record(
            year=to_int(_text(it, "dealYear")),
            month=to_int(_text(it, "dealMonth")),
            day=to_int(_text(it, "dealDay")),
            sgg_cd=_text(it, "sggCd"),
            umd_nm=_text(it, "umdNm"),
            jibun=_text(it, "jibun"),
            apt_nm=_text(it, "aptNm"),
            apt_seq=_text(it, "aptSeq"),
            area_m2=to_float(_text(it, "excluUseAr")),
            floor=to_int(_text(it, "floor")),
            build_year=to_int(_text(it, "buildYear")),
            price=to_int(_text(it, "dealAmount")),
            cdeal_type=_text(it, "cdealType"),
            cdeal_day=_text(it, "cdealDay"),
            source="api",
        ))
    return [r for r in records if r is not None], total, n_items


def fetch_month(lawd_cd, deal_ymd, session=None, key=None):
    """한 자치구·한 달치 거래를 페이지를 넘기며 전부 받는다.

    기존 코드는 1페이지(999건)만 받아서 거래가 많은 달은 잘렸다.
    totalCount를 보고 남은 페이지를 계속 요청한다.
    """
    # 윈도우 메모장으로 만든 .env는 줄 끝에 \r이 붙을 수 있어 공백을 정리한다
    key = (key or config.API_KEY).strip().strip('"').strip("'")
    # 인코딩 키(%2B, %3D 포함)를 넣어도 되도록 디코딩해서 쓴다.
    # requests가 params를 다시 인코딩하므로, 인코딩 키를 그대로 쓰면 이중 인코딩된다.
    if "%" in key:
        key = unquote(key)
    if not key:
        raise ApiError("DATA_GO_KR_KEY 환경변수가 비어 있습니다.")
    s = session or requests.Session()
    records, page = [], 1
    while True:
        resp = s.get(config.API_URL, timeout=30, params={
            "serviceKey": key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "pageNo": page,
            "numOfRows": config.ROWS_PER_PAGE,
        })
        resp.raise_for_status()           # 5xx 등은 예외 → Airflow가 재시도
        items, total, n_items = parse_items(resp.text)
        records.extend(items)
        if page * config.ROWS_PER_PAGE >= total or n_items == 0:
            break
        page += 1
    return assign_ids(records)
