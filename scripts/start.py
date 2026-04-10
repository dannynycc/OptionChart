"""
start.py — OptionChart 智慧啟動腳本
由 start.bat 呼叫，自動判斷各 process 狀態再決定要啟動什麼。

狀態矩陣：
  uvicorn ✗ / xqfap ✗ → 兩個都啟動
  uvicorn ✓ / xqfap ✗ → 只補啟動 xqfap
  uvicorn ✗ / xqfap ✓ → 只補啟動 uvicorn（xqfap 下次 push 自動重連）
  uvicorn ✓ / xqfap ✓ → 跳過啟動，直接等 data
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONITOR = os.path.join(ROOT, 'monitor')
PID_FILE = os.path.join(MONITOR, 'xqfap.pid')
SERVER_URL = "http://localhost:8000"


def server_alive() -> bool:
    try:
        with socket.create_connection(("localhost", 8000), timeout=0.1):
            return True
    except OSError:
        return False


def daqfap_alive() -> bool:
    result = subprocess.run(
        ['tasklist', '/FI', 'IMAGENAME eq daqFAP.exe', '/NH'],
        capture_output=True, text=True
    )
    return 'daqFAP.exe' in result.stdout


def xqfap_alive() -> bool:
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        result = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
            capture_output=True, text=True
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def start_uvicorn():
    print("[uvicorn] starting...")
    os.makedirs(MONITOR, exist_ok=True)
    out = open(os.path.join(MONITOR, 'uvicorn.log'), 'a')
    err = open(os.path.join(MONITOR, 'uvicorn_err.log'), 'a')
    subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '8000',
         '--no-access-log'],
        cwd=ROOT, stdout=out, stderr=err,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def start_xqfap():
    print("[xqfap ] starting...")
    os.makedirs(MONITOR, exist_ok=True)
    out = open(os.path.join(MONITOR, 'xqfap.log'), 'a')
    err = open(os.path.join(MONITOR, 'xqfap_err.log'), 'a')
    subprocess.Popen(
        [sys.executable, 'xqfap_feed.py'],
        cwd=ROOT, stdout=out, stderr=err,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


# ── 判斷狀態 ────────────────────────────────────────────────
uvicorn_up = server_alive()
xqfap_up   = xqfap_alive()

print(f"uvicorn : {'running' if uvicorn_up else 'stopped'}")
print(f"xqfap   : {'running' if xqfap_up   else 'stopped'}")

if not uvicorn_up:
    start_uvicorn()

if not xqfap_up:
    start_xqfap()

# ── 等 active series 有 data ─────────────────────────────────
print("Waiting for data...", flush=True)
wait_start = time.time()
xqfap_restarted = False

while True:
    try:
        r = urllib.request.urlopen(f"{SERVER_URL}/api/status", timeout=3)
        d = json.loads(r.read())
        if d.get("last_updated", 0) > 0:
            break
    except Exception:
        pass

    elapsed = time.time() - wait_start

    if not daqfap_alive():
        # 新富邦 e01 沒開
        if elapsed > 15:
            print("*** 偵測不到新富邦 e01（daqFAP.exe），請先開啟後再繼續 ***", flush=True)
            wait_start = time.time()
    elif not xqfap_restarted and elapsed > 30:
        # e01 有開但 30 秒無資料 → 重啟 xqfap 一次
        print("新富邦 e01 有開但無資料，嘗試重啟 xqfap...", flush=True)
        if os.path.exists(PID_FILE):
            try:
                with open(PID_FILE) as f:
                    old_pid = int(f.read().strip())
                subprocess.run(['taskkill', '/F', '/PID', str(old_pid)], capture_output=True)
            except Exception:
                pass
        start_xqfap()
        xqfap_restarted = True
        wait_start = time.time()
    elif xqfap_restarted and elapsed > 30:
        # 重啟後還是沒資料
        print("*** 仍無資料，請手動重啟新富邦 e01 後再試 ***", flush=True)
        wait_start = time.time()

    time.sleep(2)

print("Data ready.")
# 不自動開瀏覽器——前端 WebSocket 偵測到 server 重啟會自動 hard reload
# 首次使用請手動開啟 http://localhost:8000
print(f"請開啟 {SERVER_URL}")
