@echo off
chcp 65001 >nul
echo 停止 OptionChart (FastAPI + feed)...

taskkill /F /IM python.exe 2>nul && echo   OK || echo   (無執行中的 python.exe)

echo.
echo 全部停止完畢。
