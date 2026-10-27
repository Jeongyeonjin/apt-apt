"""API 연결 확인: 강남구 한 달치를 받아 건수와 예시를 출력한다. DB에는 저장하지 않는다.

사용: python -m apt_pipeline.check_api [구코드] [YYYYMM]
"""
import sys

from .api import fetch_month
from .config import API_URL, SEOUL_GU

lawd = sys.argv[1] if len(sys.argv) > 1 else "11680"
ymd = sys.argv[2] if len(sys.argv) > 2 else "202501"

print(f"요청 주소: {API_URL}")
recs = fetch_month(lawd, ymd)
print(f"{SEOUL_GU.get(lawd, lawd)} {ymd}: {len(recs):,}건, "
      f"해제 {sum(r['cdeal_type'] == 'O' for r in recs)}건")
for r in recs[:3]:
    print(f"  {r['deal_date']} {r['apt_nm']} {r['area_m2']}㎡ {r['floor']}층 "
          f"{r['price']:,}만원 (평당 {r['price_per_pyeong']:,.0f}만원)")
print("연결 정상" if recs else "0건: 키 승인 대기(최대 1~2시간) 또는 주소를 확인하세요")
