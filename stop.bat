@echo off
chcp 65001 >nul
echo 停止 Windows bridge (python.exe)...
taskkill /F /IM python.exe 2>nul && echo   OK || echo   (無執行中的 python.exe)

echo 停止 WSL uvicorn server...
wsl -d Ubuntu -- bash -c "pkill -f uvicorn 2>/dev/null && echo '  OK' || echo '  (無執行中的 uvicorn)'"

echo.
echo 全部停止完畢。
pause
