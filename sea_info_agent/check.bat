@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
rem Copied activate.bat files can point to a different checkout.
set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo 프로젝트 가상환경이 없어요. 먼저 start.bat를 실행해 주세요.
    pause
    exit /b 1
)
"%VENV_PY%" -c "import dotenv, streamlit, httpx" >nul 2>nul
if errorlevel 1 (
    echo 프로젝트 가상환경에 검사에 필요한 패키지가 없어요.
    echo "%VENV_PY%" -m pip install -r requirements.txt
    pause
    exit /b 1
)
if "%API_PORT%"=="" set API_PORT=8005
set BACKEND_URL=http://127.0.0.1:%API_PORT%
"%VENV_PY%" tools\smoke_test.py %*
set "CHECK_EXIT=%ERRORLEVEL%"
pause
exit /b %CHECK_EXIT%
