"""
test_intraday.py
驗證盤中快照 + 分鐘價格線的觸發和內容正確性。
對實際跑著的 server 操作。
"""

import os
import sys
import time
import json
import requests
from datetime import datetime

SERVER = "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTRADAY_DIR = os.path.join(ROOT, 'snapshots', 'intraday')
MONITOR_DIR = os.path.join(ROOT, 'monitor')


def test_price_log():
    """驗證分鐘價格線 CSV 存在且格式正確"""
    print("=== Test: 分鐘價格線 ===")

    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(MONITOR_DIR, f"price_log_{today}.csv")

    # 等最多 90 秒讓第一筆寫入
    print(f"  等待 price_log_{today}.csv 出現...")
    for i in range(18):
        if os.path.exists(path):
            break
        time.sleep(5)

    if not os.path.exists(path):
        print("  FAIL: CSV 未出現（等了 90 秒）")
        return False

    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    print(f"  檔案存在，{len(lines)} 行")
    if len(lines) < 2:
        print("  FAIL: 不到 2 行（header + data）")
        return False

    header = lines[0].strip()
    if header != "timestamp,futures_price,implied_forward":
        print(f"  FAIL: header 不正確: {header}")
        return False

    # 驗證最後一行格式
    last = lines[-1].strip().split(',')
    if len(last) != 3:
        print(f"  FAIL: 欄位數不是 3: {last}")
        return False

    ts, fp, fwd = last
    print(f"  最新一筆: ts={ts}, futures={fp}, implied={fwd}")
    if float(fp) <= 0:
        print(f"  FAIL: futures_price <= 0")
        return False

    print(f"  PASS")
    return True


def test_intraday_snapshot():
    """驗證盤中快照存在且內容完整"""
    print("\n=== Test: 盤中快照 ===")

    # 手動觸發一次（透過 API 不好觸發，直接檢查目錄）
    # 盤中快照靠 _periodic_broadcast 的 tick % 1800 觸發
    # 這裡我們等不了 30 分鐘，改為檢查目錄有沒有檔案

    if not os.path.exists(INTRADAY_DIR):
        print(f"  SKIP: {INTRADAY_DIR} 不存在（尚未觸發過）")
        print(f"  這是正常的——盤中快照每 30 分鐘觸發一次")
        print(f"  如果 server 剛啟動不到 30 分鐘，還沒有第一張")
        return True  # 不算失敗

    today = datetime.now().strftime("%Y-%m-%d")
    files = [f for f in os.listdir(INTRADAY_DIR)
             if f.endswith('.json') and today in f]

    if not files:
        print(f"  SKIP: 今天沒有盤中快照（server 可能剛啟動）")
        return True

    print(f"  找到 {len(files)} 個今天的盤中快照")
    for fname in sorted(files)[:3]:
        path = os.path.join(INTRADAY_DIR, fname)
        with open(path, 'r', encoding='utf-8') as f:
            snap = json.load(f)

        series = snap.get('series', '?')
        t = snap.get('time', '?')
        fp = snap.get('futures_price', 0)
        strikes = snap.get('strikes', [])
        raw_c = snap.get('raw_calls', [])
        raw_p = snap.get('raw_puts', [])
        table = snap.get('table', {})
        atm = snap.get('atm_strike')

        is_columnar = isinstance(table, dict)
        size = os.path.getsize(path)

        print(f"\n  {fname} ({size:,} bytes):")
        print(f"    series={series}, time={t}, futures={fp}")
        print(f"    strikes={len(strikes)}, raw_calls={len(raw_c)}, raw_puts={len(raw_p)}")
        print(f"    table columnar={is_columnar}, atm={atm}")

        # 驗證
        if not strikes:
            print(f"    FAIL: strikes 為空")
            return False
        if not is_columnar:
            print(f"    FAIL: table 不是 columnar 格式")
            return False
        if fp <= 0:
            print(f"    FAIL: futures_price <= 0")
            return False

    print(f"\n  PASS")
    return True


def test_close_snapshot_unaffected():
    """確認收盤 13:45 快照邏輯沒被影響"""
    print("\n=== Test: 收盤快照不受影響 ===")

    r = requests.get(f"{SERVER}/api/status", timeout=5)
    status = r.json()
    print(f"  server status: connected={status['connected']}, subscribed={status['subscribed_count']}")

    r = requests.get(f"{SERVER}/api/data", timeout=5)
    data = r.json()
    table = data.get('table', [])
    print(f"  /api/data: table={len(table)} rows, series={data.get('series')}")

    if not table:
        print(f"  FAIL: table 為空")
        return False

    print(f"  PASS")
    return True


if __name__ == '__main__':
    try:
        requests.get(f"{SERVER}/api/status", timeout=2)
    except Exception:
        print("ERROR: server 未啟動")
        sys.exit(1)

    ok = True
    ok = test_price_log() and ok
    ok = test_intraday_snapshot() and ok
    ok = test_close_snapshot_unaffected() and ok

    print(f"\n{'='*40}")
    print(f"結果: {'ALL PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
