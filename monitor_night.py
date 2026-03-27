"""
monitor_night.py — 等到 15:00 後監控 last_updated 活躍狀態
每 15 秒 poll 一次，凍結超過 90s 就標記 WARN，超過 180s 標記 DEAD
"""
import time
import urllib.request
import json
import sys
import os

API = "http://localhost:8000/api/status"
INTERVAL = 15       # 秒
WARN_SEC = 90       # 超過 90s 沒更新 → WARN
DEAD_SEC = 180      # 超過 180s 沒更新 → DEAD

_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'monitor_night.log')
os.makedirs(os.path.dirname(_log_path), exist_ok=True)
_log_file = open(_log_path, 'a', encoding='utf-8')

def _print(*args, **kwargs):
    kwargs.pop('flush', None)
    print(*args, **kwargs, flush=True)
    print(*args, **kwargs, file=_log_file, flush=True)

def fetch_ts():
    try:
        with urllib.request.urlopen(API, timeout=5) as r:
            d = json.loads(r.read())
            return d.get("last_updated")
    except Exception as e:
        return None

# ── 等到 15:00 ────────────────────────────────────────────
now = time.localtime()
target_sec = 15 * 3600  # 15:00:00
cur_sec = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec

if cur_sec < target_sec:
    wait = target_sec - cur_sec
    _print(f"[{time.strftime('%H:%M:%S')}] 等待 {wait//60} 分 {wait%60} 秒到 15:00...")
    time.sleep(wait)

_print(f"[{time.strftime('%H:%M:%S')}] ===== 夜盤監控開始 =====")
_print(f"WARN > {WARN_SEC}s, DEAD > {DEAD_SEC}s")
_print()

prev_ts = None
prev_label = ""

try:
    while True:
        ts = fetch_ts()
        now_str = time.strftime('%H:%M:%S')

        if ts is None:
            _print(f"[{now_str}] ERROR: API 無回應")
        else:
            ts_str = time.strftime('%H:%M:%S', time.localtime(ts))
            ago = round(time.time() - ts)

            if prev_ts is None or ts != prev_ts:
                delta = f"(+{round(ts - prev_ts)}s)" if prev_ts and ts != prev_ts else "(first)"
                _print(f"[{now_str}] last_updated={ts_str} {delta} ✓")
                prev_ts = ts
                prev_label = "ok"
            else:
                if ago > DEAD_SEC:
                    label = "DEAD"
                elif ago > WARN_SEC:
                    label = "WARN"
                else:
                    label = "ok"

                marker = "💀 DEAD" if label == "DEAD" else ("⚠ WARN" if label == "WARN" else "·")
                _print(f"[{now_str}] last_updated={ts_str} ({ago}s ago) {marker}")

        time.sleep(INTERVAL)

except KeyboardInterrupt:
    _print("\n監控中斷。")
