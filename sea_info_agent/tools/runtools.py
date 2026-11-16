"""실행 스크립트(start/stop/check)가 쓰는 작은 도우미. 직접 실행할 일은 거의 없어요.

    python tools/runtools.py need-install      # 설치가 필요하면 종료 코드 1
    python tools/runtools.py mark-installed
    python tools/runtools.py wait-redis 20     # Redis가 켜질 때까지 최대 20초
    python tools/runtools.py wait-api 40       # API 서버가 켜질 때까지 최대 40초
    python tools/runtools.py check-api-port   # 포트 점유 시 종료 코드 1
    python tools/runtools.py check-web-port   # 화면 포트 점유 시 종료 코드 1
    python tools/runtools.py redis-ping
"""
import hashlib
import os
import pathlib
import sys
import time
import socket
import subprocess
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT)
STAMP = ROOT / ".venv" / ".req_hash"


def _env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def req_hash() -> str:
    return hashlib.md5((ROOT / "requirements.txt").read_bytes()).hexdigest()


def redis_ok() -> bool:
    _env()
    try:
        import redis
        return bool(redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                                         socket_connect_timeout=1).ping())
    except Exception:
        return False


def api_url() -> str:
    _env()
    port = os.environ.get("API_PORT")
    return f"http://127.0.0.1:{port}" if port else os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")


def api_health():
    import httpx
    try:
        # 원격 DB의 TLS 연결 시간과 localhost IPv6 재시도 때문에 정상 서버도 2초를 넘길 수 있다.
        r = httpx.get(f"{api_url()}/health", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def api_startup_failure(log_offset: int = 0) -> str:
    """시작 실패를 즉시 안내하되 연결 문자열·인증정보·예외 원문은 출력하지 않는다."""
    log = ROOT / "logs" / "api.log"
    if not log.exists():
        return ""
    with log.open("rb") as source:
        source.seek(log_offset)
        tail = source.read().decode("utf-8", errors="replace")[-20000:]
    # A previous failed launch may remain in api.log after a successful restart.
    latest_start = tail.rfind("Started server process")
    if latest_start >= 0:
        tail = tail[latest_start:]
    if ("Application startup failed" not in tail or
            tail.rfind("Application startup complete") > tail.rfind("Application startup failed")):
        return ""
    if "EMAXCONNSESSION" in tail or "max clients reached" in tail:
        return "DB 동시 연결 한도를 초과해 API 서버가 시작되지 않았어요. Supabase 풀러 연결 설정을 확인해 주세요."
    if "Apply sea_info migrations first" in tail:
        return "DB에 필요한 테이블이 없어요. 프로젝트 마이그레이션 적용 여부를 확인해 주세요."
    if "OperationalError" in tail:
        return "DB 연결에 실패해 API 서버가 시작되지 않았어요. DB 연결 설정과 네트워크 상태를 확인해 주세요."
    return "API 서버 시작에 실패했어요. logs/api.log에서 원인을 확인해 주세요."


def check_web_port() -> int:
    try:
        port = int(os.environ.get("WEB_PORT", "8501"))
        if not 1 <= port <= 65535 or port == int(os.environ.get("API_PORT", "8005")):
            raise ValueError
    except ValueError:
        print("  ✕ WEB_PORT는 1~65535 사이의 숫자이며 API_PORT와 달라야 해요.")
        return 1

    try:
        # Streamlit listens on both IPv4 and IPv6 by default. On Windows an
        # IPv6 listener can occupy the port while an IPv4-only bind succeeds.
        addresses = [(socket.AF_INET, "0.0.0.0")]
        if socket.has_ipv6:
            addresses.append((socket.AF_INET6, "::"))
        for family, address in addresses:
            # Windows may allow a second bind when the existing listener uses
            # address reuse, even though Streamlit cannot start on that port.
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                loopback = "127.0.0.1" if family == socket.AF_INET else "::1"
                if probe.connect_ex((loopback, port)) == 0:
                    raise OSError("port is already listening")
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind((address, port))
                sock.listen(1)
    except OSError:
        print(f"  ✕ 화면 포트 {port}번을 사용할 수 없어요. 다른 화면 서버가 실행 중인지 확인해 주세요.")
        print("    기존 서버를 종료하거나 다른 WEB_PORT를 지정한 뒤 다시 실행해 주세요.")
        return 1

    print(f"  ✓ 화면 포트 {port}번을 사용할 수 있어요.")
    return 0


def release_windows_ports() -> int:
    try:
        ports = [int(os.environ.get("WEB_PORT", "8501")), int(os.environ.get("API_PORT", "8005"))]
        if any(not 1 <= port <= 65535 for port in ports) or ports[0] == ports[1]:
            raise ValueError
    except ValueError:
        print("  ✕ WEB_PORT와 API_PORT는 서로 다른 1~65535 사이의 숫자여야 해요.")
        return 1

    script = """
$ErrorActionPreference = 'Stop'
$ports = @(%s)
for ($attempt = 0; $attempt -lt 10; $attempt++) {
    $connections = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in $ports })
    if ($connections.Count -eq 0) { break }
    foreach ($processId in @($connections.OwningProcess | Sort-Object -Unique)) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            Write-Host "  Stopping port listener: PID $processId ($($process.ProcessName))"
            Stop-Process -Id $processId -ErrorAction Stop
        }
    }
    Start-Sleep -Milliseconds 300
}
""" % ",".join(map(str, ports))
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], check=False)
    except OSError:
        print("  ✕ 포트 사용 프로세스를 확인하지 못했어요.")
        return 1
    if result.returncode:
        print("  ✕ 포트 사용 프로세스를 종료하지 못했어요. 권한과 위 오류를 확인해 주세요.")
        return 1
    for _ in range(20):
        if all(not _port_listening(port) for port in ports):
            return 0
        time.sleep(0.25)
    print("  ✕ 기존 프로세스가 종료된 뒤에도 포트가 사용 중이에요. 다시 실행해 주세요.")
    return 1


def _port_listening(port: int) -> bool:
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.25)
                if sock.connect_ex((address, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def check_api_port() -> int:
    try:
        parsed = urlsplit(api_url())
        port = parsed.port if parsed.port is not None else 80
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        print("  ✕ API 포트 설정이 올바르지 않아요. API_PORT를 확인해 주세요.")
        return 1

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
                )
            sock.bind(("127.0.0.1", port))
    except OSError as exc:
        if exc.errno in (98, 10048) or getattr(exc, "winerror", None) == 10048:
            print(f"  ✕ API 포트 {port}번을 다른 프로그램이 사용 중이에요.")
            print("    기존 API 서버 창에서 Ctrl+C로 종료한 뒤 다시 실행해 주세요.")
        else:
            print(f"  ✕ API 포트 {port}번을 사용할 수 없어 실행을 중단해요.")
        return 1

    print(f"  ✓ API 포트 {port}번을 사용할 수 있어요.")
    return 0

def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    if cmd == "check-web-port":
        return check_web_port()
    if cmd == "release-windows-ports":
        return release_windows_ports()
    if cmd == "check-api-port":
        return check_api_port()
    if cmd == "need-install":
        return 0 if STAMP.exists() and STAMP.read_text().strip() == req_hash() else 1
    if cmd == "mark-installed":
        STAMP.write_text(req_hash())
        return 0
    if cmd == "redis-ping":
        return 0 if redis_ok() else 1
    if cmd == "wait-redis":
        for _ in range(n):
            if redis_ok():
                print("  ✓ Redis 준비 완료")
                return 0
            time.sleep(1)
        print("  ✕ Redis가 켜지지 않았어요. Docker Desktop이 실행 중인지 확인하고 `docker compose logs redis` 를 봐 주세요.")
        return 1
    if cmd == "wait-api":
        # Ignore failures left in api.log by earlier runs.
        log = ROOT / "logs" / "api.log"
        log_offset = log.stat().st_size if log.exists() else 0
        for _ in range(n):
            h = api_health()
            if h:
                m = h["modes"]
                print(f"  ✓ API 서버 준비 완료 ({api_url()}/docs)")
                print(f"    로그인: {'Supabase' if m['auth'] == 'supabase' else '로컬 대체 (시험 계정 explorer@example.com / sea12345)'}")
                # 2026.11.13 sunghoon 현재 기록 중인 API 로그 경로 안내
                print(f"    메일: {'Google SMTP' if m['mail'] == 'smtp' else '개발 모드 (인증 링크가 화면과 logs/api.log에 보여요)'}")
                print(f"    AI: {'Gemini' if m['ai'] == 'gemini' else '예시 결과 (.env에 GEMINI_API_KEY를 넣으면 실제 분석)'}")
                print(f"    Jira: {'연결' if m['jira'] == 'on' else '미연결 (제보는 저장돼요)'}")
                if not h.get("redis"):
                    print("    ⚠ API 서버가 Redis에 연결하지 못했어요. REDIS_URL을 확인해 주세요.")
                return 0
            failure = api_startup_failure(log_offset)
            if failure:
                print("  ✕ " + failure)
                return 1
            time.sleep(1)
        # 2026.11.13 sunghoon 서버 시작 실패 시 현재 로그의 마지막 내용 확인
        print("  ✕ API 서버가 켜지지 않았어요. logs/api.log 의 마지막 줄을 확인해 주세요.")
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
