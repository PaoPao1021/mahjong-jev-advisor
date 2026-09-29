@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 (
    echo Python 3.12 is required. Install it from python.org, then retry.
    goto fail
  )
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -c "import mahjong_jev_advisor, PySide6.QtWidgets, cv2, mss, rapidocr_onnxruntime, mahjong"
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -e .
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto fail
if /i "%~1"=="--check" goto check
".venv\Scripts\python.exe" -m mahjong_jev_advisor
if errorlevel 1 goto fail
exit /b 0
:check
".venv\Scripts\python.exe" -m mahjong_jev_advisor.preflight %2 %3 %4 %5
set "CHECK_EXIT=%errorlevel%"
pause
exit /b %CHECK_EXIT%
:fail
echo Startup failed. Read the error above. Python 3.12+ and project dependencies are required.
echo To repair dependencies: .venv\Scripts\python.exe -m pip install -e .
pause
exit /b 1
