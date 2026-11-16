"""관찰 위치를 지도에서 고르는 Streamlit 컴포넌트."""
from pathlib import Path

import streamlit.components.v1 as components

from backend.core.location import parse_map_place

_map = components.declare_component("sea_location_map", path=str(Path(__file__).parent))


# 2026.11.12 sunghoon 지도의 핀 선택 결과를 기존 장소 문자열 형식으로 저장
def choose_place(key: str, initial: str = "") -> str:
    coords = parse_map_place(initial)
    default = {"lat": coords[0], "lon": coords[1]} if coords else None
    value = _map(key=key, initial=list(coords) if coords else None, default=default)
    if isinstance(value, dict):
        try:
            lat, lon = float(value["lat"]), float(value["lon"])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return f"위도 {lat:.5f}, 경도 {lon:.5f}"
        except (KeyError, TypeError, ValueError):
            pass
    return ""
