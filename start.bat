@echo off
chcp 65001 >nul
echo ============================================
echo  OptionChart 啟動腳本
echo ============================================

set BROKER=%1
if "%BROKER%"=="" set BROKER=capital

cd /d %~dp0

if not exist "%~dp0monitor" mkdir "%~dp0monitor"

echo [1/2] 啟動 FastAPI server (main.py)...
powershell -Command "Start-Process -WindowStyle Hidden -FilePath 'python.exe' -ArgumentList '-m uvicorn main:app --host 0.0.0.0 --port 8000' -WorkingDirectory '%~dp0' -RedirectStandardOutput '%~dp0monitor\uvicorn.log' -RedirectStandardError '%~dp0monitor\uvicorn_err.log'"
echo       OK (背景執行，log → monitor\uvicorn.log)
timeout /t 2 /nobreak >nul

echo [2/2] 啟動 %BROKER% feed (%BROKER%_feed.py)...
powershell -Command "Start-Process -WindowStyle Hidden -FilePath 'python.exe' -ArgumentList '%BROKER%_feed.py' -WorkingDirectory '%~dp0' -RedirectStandardOutput '%~dp0monitor\xqfap.log' -RedirectStandardError '%~dp0monitor\xqfap_err.log'"
echo       OK (背景執行，log → monitor\xqfap.log)
echo.
echo 全部背景啟動完成。用 stop.bat 停止。
pause
