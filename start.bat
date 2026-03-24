@echo off
chcp 65001 >nul
echo ============================================
echo  OptionChart 啟動腳本
echo ============================================

echo [1/2] 啟動 FastAPI server (main.py)...
cd /d %~dp0
start "OptionChart Server" /min cmd /c "python -m uvicorn main:app --host 0.0.0.0 --port 8000 >> bridge_out.log 2>> bridge_err.log"
echo       OK - log: bridge_out.log / bridge_err.log
timeout /t 2 /nobreak >nul

echo [2/2] 啟動 Capital Bridge (capital_bridge.py)...
echo       bridge log: %~dp0bridge.log
echo.
python capital_bridge.py
pause
