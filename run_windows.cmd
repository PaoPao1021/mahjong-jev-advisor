@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 exit /b 1
)
if not exist ".venv\Scripts\mahjong-jev-advisor.exe" (
  ".venv\Scripts\python.exe" -m pip install -e .
  if errorlevel 1 exit /b 1
)
".venv\Scripts\mahjong-jev-advisor.exe"
