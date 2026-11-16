"""공식 가이드 코드표와 공식 예보 응답으로 지도 좌표를 갱신한다.

인증키/요청 URL은 출력하거나 파일에 저장하지 않는다. 홈에서는 실행하지 않는다.
python -m tools.refresh_tide_stations --codes <가이드에서 추출한 JSON 경로>
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import logging
import math
from pathlib import Path
from urllib.parse import unquote

import httpx

from backend.config import get_settings
from backend.core.timefmt import now

URL = "https://apis.data.go.kr/1192136/tideFcstHghLw/GetTideFcstHghLwApiService"
DEST = Path(__file__).resolve().parents[1] / "backend/resources/tide_stations.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", type=Path, required=True)
    args = parser.parse_args()
    codes = json.loads(args.codes.read_text(encoding="utf-8"))
    assert len(codes["forecast"]) == 166 and len(codes["observation"]) == 55
    logging.getLogger("httpx").setLevel(logging.WARNING)
    key = get_settings().tide_api_key.strip()
    if not key:
        raise SystemExit("공식 예보 API 키가 필요합니다.")
    day = now().date()

    def fetch(code, name):
        for attempt in range(2):
            try:
                response = httpx.get(URL, params={"serviceKey": unquote(key), "obsCode": code,
                    "reqDate": day.strftime("%Y%m%d"), "type": "json", "numOfRows": 300},
                    timeout=20, follow_redirects=False)
                response.raise_for_status()
                payload = response.json()
                root = payload.get("response", payload)
                if str(root["header"]["resultCode"]) not in ("00", "0"):
                    raise ValueError("provider_error")
                items = root["body"]["items"]
                rows = items.get("item", []) if isinstance(items, dict) else items
                rows = [rows] if isinstance(rows, dict) else rows
                if not rows:
                    raise ValueError("empty")
                coords = set()
                for row in rows:
                    if row["obsvtrNm"] != name or datetime.fromisoformat(row["predcDt"]).date() != day:
                        raise ValueError("target_mismatch")
                    lat, lon = float(row["lat"]), float(row["lot"])
                    if not math.isfinite(lat) or not math.isfinite(lon) or not (30 <= lat <= 40 and 120 <= lon <= 135):
                        raise ValueError("invalid_coordinates")
                    coords.add((lat, lon))
                if len(coords) != 1:
                    raise ValueError("coordinate_mismatch")
                lat, lon = coords.pop()
                return {"id": code, "name": name, "latitude": lat, "longitude": lon,
                    "observation_id": code if code in codes["observation"] else None,
                    "coordinate_source": "국립해양조사원 조석예보(고, 저조) 공식 응답",
                    "verified_at": now().isoformat(timespec="seconds"), "verified_date": day.isoformat()}
            except Exception:
                if attempt:
                    return None

    stations, failed = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, code, name): code for code, name in codes["forecast"].items()}
        for f in as_completed(futures):
            result = f.result()
            if result:
                stations.append(result)
            else:
                failed.append(futures[f])
    if failed:
        print(json.dumps({"verified": len(stations), "failed_codes": failed}, ensure_ascii=True))
        raise SystemExit("전체 좌표 검증에 실패해 기존 목록을 교체하지 않았습니다.")
    result = {"guide_source": "오픈API 활용가이드_조석예보(고, 저조).hwp / 조위관측소 최신 관측데이터.hwp",
        "forecast_source_url": "https://www.data.go.kr/data/15156018/openapi.do",
        "observation_source_url": "https://www.data.go.kr/data/15155508/openapi.do",
        "updated_at": now().isoformat(timespec="seconds"), "observation_stations": codes["observation"],
        "stations": sorted(stations, key=lambda s: s["id"])}
    DEST.parent.mkdir(parents=True, exist_ok=True)
    temp = DEST.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(DEST)
    print(json.dumps({"verified": len(stations), "common_codes": sum(bool(s["observation_id"]) for s in stations)}))


if __name__ == "__main__":
    main()
