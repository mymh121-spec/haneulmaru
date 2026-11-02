@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if errorlevel 1 (
        python -m venv .venv
    ) else (
        py -3 -m venv .venv
    )
)
if not exist ".venv\Scripts\python.exe" goto :error
".venv\Scripts\python.exe" -c "import cv2, numpy" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :error
)
".venv\Scripts\python.exe" run.py --config demo\config.json --source demo\multi-tracking.avi --recording --show
if errorlevel 1 goto :error
exit /b 0
:error
echo Python 3.10 이상, 패키지 설치와 위의 실행 오류를 확인하세요.
pause
exit /b 1
