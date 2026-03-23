#!/bin/bash
# start_server.sh - 單獨啟動 WSL FastAPI server（不含 Windows bridge）
# 用法：bash start_server.sh
#       或直接從 Windows 呼叫：wsl -d Ubuntu -- bash ~/OptionChart/start_server.sh

set -e
cd "$(dirname "$(realpath "$0")")"

echo "停止舊的 uvicorn（若有）..."
pkill -f uvicorn 2>/dev/null && echo "  已停止舊 instance" || echo "  (無舊 instance)"
sleep 1

echo "啟動 FastAPI server (port 8000)..."
echo "log 輸出至 /tmp/uvicorn.log"
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
