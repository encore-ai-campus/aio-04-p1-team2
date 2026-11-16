"""공식 응답으로 좌표를 검증한 예보지점. 외부 요청 없이 읽는다."""
import json
import math
from functools import lru_cache
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "resources/tide_stations.json"


@lru_cache
def station_catalog() -> dict:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    stations = data["stations"]
    if len(stations) != 166 or len({s["id"] for s in stations}) != 166:
        raise ValueError("공식 예보지점 목록을 확인해 주세요.")
    observations = data["observation_stations"]
    for s in stations:
        if not (math.isfinite(s["latitude"]) and math.isfinite(s["longitude"])
                and 30 <= s["latitude"] <= 40 and 120 <= s["longitude"] <= 135):
            raise ValueError("공식 좌표가 잘못되었습니다.")
        if s["observation_id"] and observations.get(s["observation_id"]) != s["name"]:
            raise ValueError("관측소 연결이 잘못되었습니다.")
    return data


@lru_cache
def stations_by_id() -> dict:
    return {s["id"]: s for s in station_catalog()["stations"]}
