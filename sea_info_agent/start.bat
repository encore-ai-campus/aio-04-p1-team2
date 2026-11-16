@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if "%API_PORT%"=="" set API_PORT=8005
if "%WEB_PORT%"=="" set WEB_PORT=8501

echo.
echo [1/6] Python 확인
set PY="%CD%\.venv\Scripts\python.exe"
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul && goto python_ready
set PY=py -3.13
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul && goto python_ready
set PY=python
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul && goto python_ready
where uv >nul 2>nul || goto python_missing
for /f "delims=" %%P in ('uv python find --no-python-downloads 3.13 2^>nul') do set PY="%%P"
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul && goto python_ready
:python_missing
echo   Python 3.13을 설치해 주세요: https://www.python.org/downloads/
echo   uv를 사용한다면: uv python install 3.13
pause
exit /b 1
:python_ready
%PY% --version

echo [2/6] 가상환경 (.venv)
if not exist .venv goto create_venv
.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>nul && goto activate_venv
echo   기존 가상환경의 버전이 달라 백업하고 Python 3.13으로 다시 만들어요.
ren .venv .venv-backup-%RANDOM% || (echo   가상환경을 사용하는 서버와 터미널을 닫고 다시 실행해 주세요. & pause & exit /b 1)
:create_venv
%PY% -m venv .venv || (echo   가상환경 생성에 실패했어요. 위 오류를 확인해 주세요. & pause & exit /b 1)
:activate_venv
rem Use this checkout directly: copied activate.bat files may contain old paths.
set "VIRTUAL_ENV=%CD%\.venv"
set "VENV_PY=%CD%\.venv\Scripts\python.exe"
set "PATH=%CD%\.venv\Scripts;%PATH%"

echo.
rem Stop before installation, Redis, or log redirection if the API port is occupied.
echo [확인] 화면과 API 포트 충돌 검사

".venv\Scripts\python.exe" -B tools\runtools.py release-windows-ports
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -B tools\runtools.py check-web-port
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -B tools\runtools.py check-api-port
if errorlevel 1 (
  echo.
  echo 기존 프로그램은 자동으로 종료하지 않았어요.
  echo 서버를 종료하거나 다른 API_PORT를 지정한 뒤 다시 실행해 주세요.
  pause
  exit /b 1
)

echo [3/6] 필요한 프로그램 설치
"%VENV_PY%" tools\runtools.py need-install
if errorlevel 1 (
  echo   처음이거나 목록이 바뀌어서 설치해요. 몇 분 걸려요...
  "%VENV_PY%" -m pip install -q --upgrade pip || (echo   pip 업데이트에 실패했어요. 위 오류를 확인해 주세요. & pause & exit /b 1)
  "%VENV_PY%" -m pip install -q -r requirements.txt || (echo   설치에 실패했어요. 위의 패키지명과 오류 내용을 확인해 주세요. & pause & exit /b 1)
  "%VENV_PY%" tools\runtools.py mark-installed
)

echo [4/6] 환경변수 파일 (.env)
if not exist .env (copy .env.example .env >nul & echo   .env 를 새로 만들었어요. 키를 넣으면 실제 서비스로 바뀌어요.)
if not exist logs mkdir logs
if not exist data mkdir data

echo [5/6] Redis (Docker)
"%VENV_PY%" tools\runtools.py redis-ping
if errorlevel 1 (
  docker compose up -d redis || (echo   Docker Desktop을 설치하고 실행한 뒤 다시 해 주세요. & pause & exit /b 1)
)
"%VENV_PY%" tools\runtools.py wait-redis 30 || (pause & exit /b 1)

echo [6/6] API 서버 - 화면
rem 2026.11.13 sunghoon API 로그를 api.log에 기록하고 날짜 변경 시 보관
start "Sea-Info API" /min cmd /c ""%VENV_PY%" tools\run_api.py --host 127.0.0.1 --port %API_PORT%"
echo   API 서버 준비를 기다리고 있어요. 문제가 있으면 원인을 표시해요.
"%VENV_PY%" tools\runtools.py wait-api 60 || (pause & exit /b 1)
set BACKEND_URL=http://127.0.0.1:%API_PORT%
echo.
echo   화면 주소: http://localhost:%WEB_PORT%   (끄려면 이 창에서 Ctrl+C, 모두 끄려면 stop.bat)
"%VENV_PY%" -m streamlit run app.py --server.port %WEB_PORT%
if errorlevel 1 (
  echo   화면 서버 실행에 실패했어요. 위 오류를 확인해 주세요.
  pause
  exit /b 1
)
