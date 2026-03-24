@echo off
chcp 65001 >nul
echo ============================================
echo  OptionChart 啟動腳本
echo ============================================

set BROKER=%1
if "%BROKER%"=="" set BROKER=capital

cd /d %~dp0

echo [1/2] 啟動 FastAPI server (main.py)...
powershell -Command "Start-Process -WindowStyle Hidden -FilePath 'python.exe' -ArgumentList '-m uvicorn main:app --host 0.0.0.0 --port 8000' -WorkingDirectory '%~dp0'"
echo       OK (背景執行，無視窗)
timeout /t 2 /nobreak >nul

echo [2/2] 啟動 %BROKER% feed (%BROKER%_feed.py)...
powershell -Command "Start-Process -WindowStyle Hidden -FilePath 'python.exe' -ArgumentList '%BROKER%_feed.py' -WorkingDirectory '%~dp0'"
echo       OK (背景執行，無視窗)
echo.
echo 全部背景啟動完成。用 stop.bat 停止。
pause
