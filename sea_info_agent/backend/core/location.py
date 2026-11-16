"""지도 위치와 기존 텍스트 장소의 공통 처리."""
from __future__ import annotations

import io
import re

# 2026.11.12 sunghoon 지도 좌표는 지역명과 구별해 공유 시 정확한 위치 노출 방지
_COORDINATES = re.compile(r"^위도\s*(-?\d+(?:\.\d+)?),\s*경도\s*(-?\d+(?:\.\d+)?)$")


def parse_map_place(place: str) -> tuple[float, float] | None:
    match = _COORDINATES.fullmatch((place or "").strip())
    if not match:
        return None
    lat, lon = map(float, match.groups())
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


# 2026.11.12 sunghoon 업로드 원본의 EXIF GPS 좌표를 지도 초기 핀 위치로 변환
def photo_gps_place(image_bytes: bytes) -> str:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            gps = image.getexif().get_ifd(34853)
        if not gps:
            return ""

        def coordinate(values, ref, positive, negative, limit):
            if isinstance(ref, bytes):
                ref = ref.decode("ascii")
            if ref not in (positive, negative) or len(values) != 3:
                raise ValueError("invalid GPS metadata")
            degrees, minutes, seconds = (float(v) for v in values)
            if not (0 <= degrees <= limit and 0 <= minutes < 60 and 0 <= seconds < 60):
                raise ValueError("invalid GPS coordinate")
            value = degrees + minutes / 60 + seconds / 3600
            if value > limit:
                raise ValueError("invalid GPS coordinate")
            return -value if ref == negative else value

        lat = coordinate(gps[2], gps[1], "N", "S", 90)
        lon = coordinate(gps[4], gps[3], "E", "W", 180)
        return f"위도 {lat:.5f}, 경도 {lon:.5f}"
    except (OSError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, OverflowError, AttributeError, UnicodeError):
        return ""
