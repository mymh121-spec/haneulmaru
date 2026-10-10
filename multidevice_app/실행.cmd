@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 Python 3.10 이상 설치 후 안내문에 따라 환경을 준비하세요.
  pause
  exit /b 1
)
set /p "HM_S22_SOURCE=S22 영상 스트림 주소 또는 녹화 파일: "
if not defined HM_S22_SOURCE exit /b 1
".venv\Scripts\python.exe" run.py --source "%HM_S22_SOURCE%" --show %*
pause
