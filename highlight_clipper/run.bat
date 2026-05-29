@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================
echo   游戏高光自动剪辑工具
echo ================================
echo.
echo 首次使用请确保已安装依赖:
echo   pip install requests yt-dlp
echo.
python main.py
pause
