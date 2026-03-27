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
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONITOR = os.path.join(ROOT, 'monitor')
PID_FILE = os.path.join(MONITOR, 'xqfap.pid')
SERVER_URL = "http://localhost:8000"


def server_alive() -> bool:
    try:
        urllib.request.urlopen(f"{SERVER_URL}/api/status", timeout=2)
        return True
    except Exception:
        return False


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
        [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '8000'],
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
while True:
    try:
        r = urllib.request.urlopen(f"{SERVER_URL}/api/status", timeout=3)
        d = json.loads(r.read())
        if d.get("last_updated", 0) > 0:
            break
    except Exception:
        pass
    time.sleep(2)

print("Data ready. Opening browser...")
webbrowser.open(SERVER_URL)
