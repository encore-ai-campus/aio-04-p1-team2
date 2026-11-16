"""관리자에게 현재 및 날짜별 API 로그의 최근 부분을 제공해요."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.core.log_safety import sanitize_log
from backend.core.security import require_admin
from backend.models import User

router = APIRouter(tags=["관리자 로그"])
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
MAX_BYTES = 1024 * 1024


class LogOut(BaseModel):
    filename: str
    lines: list[str]
    truncated: bool


# 2026.11.13 sunghoon 로그 끝부분만 제한된 크기로 읽고 기존 로그의 민감 정보도 가림
def read_tail(path: Path, count: int) -> tuple[list[str], bool]:
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        start = max(size - MAX_BYTES, 0)
        f.seek(start)
        data = f.read(MAX_BYTES)
    if start:
        _, separator, data = data.partition(b"\n")
        if not separator:
            return [], True
    lines = data.decode("utf-8", errors="replace").splitlines()
    return [sanitize_log(line) for line in lines[-count:]], start > 0 or len(lines) > count


# 2026.11.13 sunghoon 기존 관리자 작업 이력 API와 주소를 분리하여 파일 로그 조회
@router.get("/admin/api-logs", response_model=LogOut, summary="API 로그 조회")
def get_logs(day: date | None = None, lines: int = Query(200, ge=1, le=500),
             admin: User = Depends(require_admin)):
    filename = f"api_{day.isoformat()}.log" if day else "api.log"
    root = LOG_DIR.resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "해당 날짜의 로그가 없어요." if day else "현재 로그 파일이 없어요.")
    try:
        result, truncated = read_tail(path, lines)
    except OSError:
        raise HTTPException(503, "로그를 읽지 못했어요. 잠시 뒤 다시 시도해 주세요.") from None
    return LogOut(filename=filename, lines=result, truncated=truncated)
