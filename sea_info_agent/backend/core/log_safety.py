"""로그에 포함될 수 있는 인증 정보 제거."""
from __future__ import annotations

import re

# 2026.11.13 sunghoon URL 쿼리와 인증 정보가 로그에 남거나 관리자 화면에 노출되지 않도록 처리
_URL_QUERY = re.compile(r"(https?://[^\s\"'<>?]+|/[^\s\"'<>?]*)\?[^\s\"'<>]+", re.IGNORECASE)
_DB_URL = re.compile(r"(postgres(?:ql)?(?:\+\w+)?://)[^\s@]+@", re.IGNORECASE)
_BEARER = re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/-]+=*", re.IGNORECASE)
_SECRET = re.compile(r"((?:password|passwd|secret|api[_-]?key|token|authorization)[\"']?\s*[=:]\s*[\"']?)[^\s&,\"'}]+", re.IGNORECASE)


def sanitize_log(text: str) -> str:
    text = _DB_URL.sub(r"\1[가림]@", text)
    text = _URL_QUERY.sub(r"\1?[가림]", text)
    text = _BEARER.sub(r"\1[가림]", text)
    return _SECRET.sub(r"\1[가림]", text)
