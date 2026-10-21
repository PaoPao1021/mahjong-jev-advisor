@echo off
setlocal
set "MAHJONG_ADVISOR_EDITION=steam"
call "%~dp0run_windows.cmd" %*
exit /b %errorlevel%
