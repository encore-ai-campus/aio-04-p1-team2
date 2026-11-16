"""공식 지점 지도. iframe에는 공개 자료만 전달하고 API 키는 서버에 둔다."""
from pathlib import Path

import streamlit.components.v1 as components

_map = components.declare_component("sea_tide_map", path=str(Path(__file__).parent))


def tide_map(stations: list[dict], response: dict | None = None):
    return _map(stations=stations, response=response, key="official_tide_map", default=None)
