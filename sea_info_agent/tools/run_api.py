"""API 서버의 표준 출력과 오류를 한국 날짜별 로그로 기록해 실행해요."""
from __future__ import annotations

import argparse
import io
import logging
import shutil
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))
sys.path.insert(0, str(ROOT))
from backend.core.log_safety import sanitize_log  # noqa: E402


# 2026.11.13 sunghoon 현재 로그는 api.log에 기록하고 날짜 변경 시 이전 날짜 파일로 보관
class DailyLogStream(io.TextIOBase):
    def __init__(self, folder: Path):
        self.folder = folder
        self.lock = threading.RLock()
        self.pending = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if not text:
            return 0
        with self.lock:
            # 2026.11.13 sunghoon 나뉘어 출력된 URL도 한 줄로 합쳐 민감정보를 가린 뒤 기록
            self.pending += text
            if "\n" not in self.pending:
                return len(text)
            complete, self.pending = self.pending.rsplit("\n", 1)
            safe_text = sanitize_log(complete + "\n")
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / "api.log"
            today = datetime.now(KST).date()
            if path.exists():
                previous = datetime.fromtimestamp(path.stat().st_mtime, KST).date()
                if previous != today:
                    archive = self.folder / f"api_{previous:%Y-%m-%d}.log"
                    if archive.exists():
                        # 2026.11.13 sunghoon 같은 날짜의 보관 파일이 있으면 기존 로그를 보존하며 이어 붙임
                        with path.open("rb") as source, archive.open("ab") as target:
                            shutil.copyfileobj(source, target)
                        path.unlink()
                    else:
                        path.rename(archive)
            with path.open("a", encoding="utf-8", errors="backslashreplace") as output:
                output.write(safe_text)
        return len(text)

    def flush(self) -> None:
        pass


# 2026.11.13 sunghoon 자동 갱신 성공 요청이 로그를 반복해서 채우지 않도록 제외
class MonitoringAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 5 and args[1] == "GET"
                    # 2026.11.13 sunghoon 파일 로그 조회만 제외하고 관리자 작업 이력 요청은 유지
                    and str(args[2]).split("?", 1)[0] == "/admin/api-logs" and args[4] == 200)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    stream = DailyLogStream(ROOT / "logs")
    sys.stdout = sys.stderr = stream
    import uvicorn
    # 2026.11.13 sunghoon Uvicorn 로깅 설정 이후 모니터링 조회 필터를 적용해 API 실행
    config = uvicorn.Config("backend.main:app", host=args.host, port=args.port)
    logging.getLogger("uvicorn.access").addFilter(MonitoringAccessFilter())
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
