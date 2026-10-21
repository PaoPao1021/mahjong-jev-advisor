@echo off
setlocal
set "MAHJONG_ADVISOR_EDITION=browser"
call "%~dp0run_windows.cmd" %*
exit /b %errorlevel%
