"""
bench_http.py
直接對目前跑的 OptionChart server (localhost:8000) 做 HTTP 效能對比。
測 /api/feed 和 /api/heartbeat（xqfap_feed 最常呼叫的兩個端點）。
"""

import time
import threading
import requests

URL = "http://127.0.0.1:8000"
N = 300  # 每項測試的請求數

# 測試用 payload（模擬 quote_poll_worker 推送）
FEED_PAYLOAD = [{"symbol": "TXON04C33000", "trade_volume": 0,
                 "bid_price": 100.0, "ask_price": 101.0, "last_price": 100.5}]


def _bench_sequential(label, method, url, **kwargs):
    t0 = time.time()
    ok = 0
    for _ in range(N):
        try:
            r = method(url, timeout=5, **kwargs)
            if r.status_code == 200:
                ok += 1
        except Exception:
            pass
    elapsed = time.time() - t0
    rps = N / elapsed if elapsed > 0 else 0
    print(f"  {label}: {elapsed:.2f}s  ({rps:.0f} req/s)  ok={ok}/{N}")
    return elapsed


def _bench_concurrent(label, method, url, n_threads=8, **kwargs):
    """模擬多 thread 同時打"""
    ok = [0]
    lock = threading.Lock()
    per_thread = N // n_threads

    def worker():
        for _ in range(per_thread):
            try:
                r = method(url, timeout=5, **kwargs)
                if r.status_code == 200:
                    with lock:
                        ok[0] += 1
            except Exception:
                pass

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0
    total = per_thread * n_threads
    rps = total / elapsed if elapsed > 0 else 0
    print(f"  {label}: {elapsed:.2f}s  ({rps:.0f} req/s)  ok={ok[0]}/{total}")
    return elapsed


def main():
    # 確認 server 活著
    try:
        requests.get(f"{URL}/api/status", timeout=2)
    except Exception:
        print("ERROR: OptionChart server not running on :8000")
        return

    print(f"=== HTTP Benchmark (N={N}) ===\n")

    # ── 無 Session（現行做法）──────────────────────
    print("[A] 無 Session（每次新 TCP）")
    _bench_sequential("feed seq", requests.post,
                      f"{URL}/api/feed?series=TXON04", json=FEED_PAYLOAD)
    _bench_sequential("heartbeat seq", requests.post,
                      f"{URL}/api/heartbeat?series=TXON04")
    _bench_concurrent("feed 8-thread", requests.post,
                      f"{URL}/api/feed?series=TXON04", json=FEED_PAYLOAD)

    # ── 有 Session ────────────────────────────────
    session = requests.Session()
    print("\n[B] 有 Session（keep-alive 連線重用）")
    _bench_sequential("feed seq", session.post,
                      f"{URL}/api/feed?series=TXON04", json=FEED_PAYLOAD)
    _bench_sequential("heartbeat seq", session.post,
                      f"{URL}/api/heartbeat?series=TXON04")
    _bench_concurrent("feed 8-thread", session.post,
                      f"{URL}/api/feed?series=TXON04", json=FEED_PAYLOAD)
    session.close()


if __name__ == '__main__':
    main()
