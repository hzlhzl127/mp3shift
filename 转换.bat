@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "SCRIPT=%~dp0mp4_to_mp3.py"

where py >nul 2>nul
if %errorlevel%==0 (
    py "%SCRIPT%" %*
) else (
    python "%SCRIPT%" %*
)

echo.
pause
