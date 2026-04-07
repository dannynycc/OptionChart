"""
test_requests_session.py
驗證 requests.Session() 在 OptionChart 使用情境下的可靠性：
  1. 多執行緒高併發（模擬 advise_worker / quote_poll / bg_poll 等同時打 localhost）
  2. Server 重啟後 pooled connection 變 stale 的自動恢復
  3. Session vs 無 Session 效能對比
"""

import threading
import time
import sys
import os
import socket
import subprocess
import requests

_MINI_SERVER_CODE = '''
from fastapi import FastAPI
app = FastAPI()
_count = 0

@app.post("/api/feed")
async def feed(series: str = ""):
    global _count
    _count += 1
    return {"ok": True, "count": _count}

@app.get("/api/status")
async def status():
    return {"ok": True}
'''

_SERVER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_mini_server.py')


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _start_server(port):
    with open(_SERVER_FILE, 'w') as f:
        f.write(_MINI_SERVER_CODE)
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', '_mini_server:app',
         '--host', '127.0.0.1', '--port', str(port)],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for _ in range(80):
        try:
            requests.get(f"http://127.0.0.1:{port}/api/status", timeout=0.5)
            return proc
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError(f"mini server failed to start on port {port}")


def _stop_server(proc):
    proc.kill()
    proc.wait()


def _cleanup():
    if os.path.exists(_SERVER_FILE):
        os.remove(_SERVER_FILE)


# ── Test 1: 多執行緒高併發 ─────────────────────────────────

def test_concurrent_session():
    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"
    proc = _start_server(port)
    try:
        session = requests.Session()
        errors = []
        lock = threading.Lock()
        successes = [0]

        N_THREADS = 8
        N_REQUESTS = 100

        def worker(tid):
            for i in range(N_REQUESTS):
                try:
                    r = session.post(
                        f"{url}/api/feed?series=T{tid}",
                        json=[{"symbol": f"T{tid}C{i}", "trade_volume": i}],
                        timeout=5,
                    )
                    if r.status_code == 200:
                        with lock:
                            successes[0] += 1
                    else:
                        with lock:
                            errors.append(f"T{tid} req{i}: HTTP {r.status_code}")
                except Exception as e:
                    with lock:
                        errors.append(f"T{tid} req{i}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - t0

        total = N_THREADS * N_REQUESTS
        print(f"[Test 1] multi-thread concurrent Session")
        print(f"  {N_THREADS} threads x {N_REQUESTS} req = {total} total")
        print(f"  OK: {successes[0]}/{total}  errors: {len(errors)}")
        print(f"  time: {elapsed:.2f}s ({total/elapsed:.0f} req/s)")
        if errors:
            for e in errors[:5]:
                print(f"    {e}")

        session.close()
        assert len(errors) == 0, f"{len(errors)} errors"
        assert successes[0] == total
        print(f"  PASS")
    finally:
        _stop_server(proc)


# ── Test 2: Server 重啟後 stale connection ────────────────

def test_stale_connection():
    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"
    proc = _start_server(port)
    session = requests.Session()
    try:
        # Phase 1: build connection pool
        for i in range(5):
            r = session.post(f"{url}/api/feed?series=S", json=[], timeout=5)
            assert r.status_code == 200

        print(f"\n[Test 2] stale connection after server restart")
        print(f"  Phase 1: 5 req OK, pool established")

        # Phase 2: kill
        _stop_server(proc)
        time.sleep(1)
        print(f"  Phase 2: server killed")

        # Phase 3: restart on SAME port
        proc = _start_server(port)
        print(f"  Phase 3: server restarted")

        # Phase 4: resume with same Session
        errors = []
        for i in range(10):
            try:
                r = session.post(f"{url}/api/feed?series=S", json=[], timeout=5)
                if r.status_code != 200:
                    errors.append(f"req{i}: HTTP {r.status_code}")
            except Exception as e:
                errors.append(f"req{i}: {e}")

        print(f"  Phase 4: 10 req, errors: {len(errors)}")
        if errors:
            for e in errors:
                print(f"    {e}")
            print(f"  FAIL - Session cannot auto-recover from server restart")
        else:
            print(f"  PASS - Session auto-recovered, zero errors")

        session.close()
    finally:
        _stop_server(proc)


# ── Test 3: 效能對比 ─────────────────────────────────────

def test_performance():
    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"
    proc = _start_server(port)
    try:
        N = 200

        # no session (current approach)
        t0 = time.time()
        for i in range(N):
            requests.post(f"{url}/api/feed?series=P", json=[], timeout=5)
        t_no = time.time() - t0

        # with session
        session = requests.Session()
        t0 = time.time()
        for i in range(N):
            session.post(f"{url}/api/feed?series=P", json=[], timeout=5)
        t_yes = time.time() - t0
        session.close()

        speedup = t_no / t_yes if t_yes > 0 else 0
        print(f"\n[Test 3] performance ({N} sequential requests)")
        print(f"  no Session:   {t_no:.2f}s ({N/t_no:.0f} req/s)")
        print(f"  with Session: {t_yes:.2f}s ({N/t_yes:.0f} req/s)")
        print(f"  speedup: {speedup:.2f}x")
        print(f"  PASS")
    finally:
        _stop_server(proc)


if __name__ == '__main__':
    try:
        test_concurrent_session()
        test_stale_connection()
        test_performance()
    finally:
        _cleanup()
