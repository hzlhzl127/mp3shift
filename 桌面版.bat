@echo off
chcp 65001 >nul
where pythonw >nul 2>nul
if errorlevel 1 (
    echo pythonw not found. Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)
start "" pythonw "%~dp0mp4_to_mp3_gui.py"
