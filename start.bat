@echo off
chcp 65001 >nul
echo ============================================
echo  OptionChart 啟動腳本
echo ============================================

echo [1/2] 啟動 WSL FastAPI server...
wsl -d Ubuntu -- bash -c "pkill -f uvicorn 2>/dev/null; sleep 1; cd ~/OptionChart && nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 >> /tmp/uvicorn.log 2>&1 &"
echo       OK - log: wsl /tmp/uvicorn.log
timeout /t 2 /nobreak >nul

echo [2/2] 啟動 Windows Bridge (skcom_bridge.py)...
echo       bridge log: %~dp0bridge.log
echo.
cd /d %~dp0
python skcom_bridge.py
pause
