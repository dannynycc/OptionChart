@echo off
cd /d %~dp0
echo 檢查並安裝依賴套件...
pip install -r requirements.txt -q
python scripts\start.py
