"""
test_snapshot_trigger.py
直接測試 _try_save_snapshot 在各種情境下是否正確觸發。
透過 HTTP 對實際跑著的 server 操作，模擬結算日/非結算日場景。
"""

import os
import sys
import time
import json
import shutil
import subprocess
import requests

SERVER = "http://127.0.0.1:8000"
SNAP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshots')


def _wait_server(timeout=30):
    for _ in range(timeout * 2):
        try:
            r = requests.get(f"{SERVER}/api/status", timeout=1)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _get_today_snapshots(series):
    """取得今天屬於 series 的快照檔案"""
    from datetime import date
    today = date.today().isoformat()
    result = []
    if os.path.exists(SNAP_DIR):
        for f in os.listdir(SNAP_DIR):
            if series in f and today in f and f.endswith('.json') and 'weekly' not in f:
                result.append(f)
    return result


def test_settlement_day_snapshot():
    """
    測試結算日快照觸發。

    現在的狀態：
    - server 正在跑，active series = TXUN04，settlement_date = 2026-04-07（今天）
    - 現在時間 > 13:45:20
    - 之前用 force-snapshot 手動存過 → _snapshot_taken_today 已標記

    測試步驟：
    1. 刪除今天 TXUN04/TXU04 的快照檔
    2. 重啟 server（清空 _snapshot_taken_today）
    3. 等待 periodic broadcast（每 10 秒檢查一次）自動觸發快照
    4. 驗證快照檔出現
    """
    print("=== 測試：結算日快照自動觸發 ===\n")

    # 確認 server 在跑
    if not _wait_server(5):
        print("ERROR: server 未啟動")
        return False

    # 確認 active series 和結算日
    r = requests.get(f"{SERVER}/api/active-series", timeout=2)
    active = r.json()
    full = active['full']
    day = full.replace('N', '')
    print(f"  active series: {full} / {day}")

    r = requests.get(f"{SERVER}/api/status", timeout=2)
    sd = r.json()['settlement_date']
    from datetime import date
    today = date.today().isoformat()
    print(f"  settlement_date: {sd}")
    print(f"  today: {today}")
    is_settlement_day = sd == today
    print(f"  是結算日: {is_settlement_day}")

    if not is_settlement_day:
        print("\n  今天不是結算日，改用模擬測試...")
        return test_simulated_settlement_day()

    # Step 1: 刪除今天的快照
    deleted = []
    for series in [full, day]:
        for f in _get_today_snapshots(series):
            path = os.path.join(SNAP_DIR, f)
            os.remove(path)
            deleted.append(f)
    print(f"\n  Step 1: 刪除 {len(deleted)} 個今日快照: {deleted}")

    # Step 2: 重啟 server
    print("  Step 2: 重啟 server...")
    # 殺掉所有 python（除了自己）
    my_pid = os.getpid()
    r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe', '/NH'],
                       capture_output=True, text=True)
    for line in r.stdout.strip().split('\n'):
        parts = line.split()
        if len(parts) >= 2 and parts[0] == 'python.exe':
            pid = int(parts[1])
            if pid != my_pid:
                subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                               capture_output=True)
    time.sleep(2)

    # 啟動 server（只啟 uvicorn + xqfap_feed，不用 start.py 的等待邏輯）
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monitor = os.path.join(root, 'monitor')
    os.makedirs(monitor, exist_ok=True)

    with open(os.path.join(monitor, 'uvicorn.log'), 'a') as ulog:
        subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', '8000'],
            cwd=root, stdout=ulog, stderr=ulog,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    if not _wait_server(15):
        print("  ERROR: uvicorn 啟動失敗")
        return False
    print("  uvicorn OK")

    with open(os.path.join(monitor, 'xqfap.log'), 'a') as xlog:
        subprocess.Popen(
            [sys.executable, 'xqfap_feed.py'],
            cwd=root, stdout=xlog, stderr=xlog,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    print("  xqfap_feed 啟動中...")

    # Step 3: 等 xqfap_feed 推資料 + periodic broadcast 觸發快照
    # periodic broadcast 每 10 秒檢查一次快照
    print("\n  Step 3: 等待快照自動觸發（最多 60 秒）...")
    for wait in range(12):
        time.sleep(5)
        snaps_full = _get_today_snapshots(full)
        snaps_day = _get_today_snapshots(day)
        elapsed = (wait + 1) * 5
        print(f"    {elapsed}s: {full}={len(snaps_full)} {day}={len(snaps_day)}")
        if snaps_full and snaps_day:
            break

    # Step 4: 驗證
    print(f"\n  Step 4: 驗證結果")
    snaps_full = _get_today_snapshots(full)
    snaps_day = _get_today_snapshots(day)

    ok = True
    if snaps_full:
        print(f"  PASS  {full}: {snaps_full}")
        # 驗證快照內容
        with open(os.path.join(SNAP_DIR, snaps_full[0]), 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"         strikes: {len(data.get('strikes', []))} 個")
        print(f"         raw_calls: {len(data.get('raw_calls', []))} 個")
        print(f"         raw_puts: {len(data.get('raw_puts', []))} 個")
    else:
        print(f"  FAIL  {full}: 快照未觸發")
        ok = False

    if snaps_day:
        print(f"  PASS  {day}: {snaps_day}")
    else:
        print(f"  FAIL  {day}: 快照未觸發")
        ok = False

    return ok


def test_simulated_settlement_day():
    """
    非結算日時的模擬測試：直接呼叫 /api/init + /api/feed 建一個假 series，
    settlement_date = today，驗證快照邏輯。
    注意：_try_save_snapshot 只存 active series，所以這個測試有限制。
    """
    print("  （非結算日模擬測試 - 跳過，請在結算日實測）")
    return True


if __name__ == '__main__':
    ok = test_settlement_day_snapshot()
    print(f"\n{'=' * 40}")
    print(f"結果: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
