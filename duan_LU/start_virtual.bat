@echo off
chcp 65001 >nul
cd /d "%~dp0"
python virtual_main.py
if errorlevel 1 (
    echo.
    echo 启动失败，请确认已安装 Python 3.8+ 且已执行:
    echo   pip install -r requirements.txt
)
pause
