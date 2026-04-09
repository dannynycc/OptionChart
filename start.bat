@echo off
chcp 65001 >nul
cd /d %~dp0
echo 檢查並安裝依賴套件...
python -m pip install -r requirements.txt -q
python scripts\start.py
