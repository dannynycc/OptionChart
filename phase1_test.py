"""
Phase 1 驗證腳本
Step 1.1: 登入測試
Step 1.2: 取得近月 TXO 月選合約列表
Step 1.3: WebSocket 訂閱，確認欄位
Step 1.4: 計算淨CALL/PUT 樣本
"""

import time
import json
import config
from fubon_neo.sdk import FubonSDK

# ── Step 1.1: 登入 ────────────────────────────────────────
print("=== Step 1.1: 登入 ===")
sdk = FubonSDK()
accounts = sdk.login(config.ID, config.PASSWORD, config.CERT_PATH, config.CERT_PASS)
print(f"登入成功，帳號: {accounts.data[0]}")
sdk.init_realtime()
print("init_realtime OK\n")

# ── Step 1.2: 取得合約列表，篩選近月月選 ─────────────────
print("=== Step 1.2: 近月 TXO 月選合約列表 ===")
restfut = sdk.marketdata.rest_client.futopt

result = restfut.intraday.tickers(
    type="OPTION",
    exchange="TAIFEX",
    session="REGULAR",
)

raw_tickers = result.data if hasattr(result, 'data') else result.get('data', [])
print(f"全部合約數: {len(raw_tickers)}")

def get_field(obj, key):
    if isinstance(obj, dict):
        return obj.get(key, '')
    return getattr(obj, key, '')

# 把 tickers 轉成 dict 方便操作
tickers = []
for t in raw_tickers:
    tickers.append({
        'symbol':         get_field(t, 'symbol'),
        'name':           get_field(t, 'name'),
        'settlementDate': get_field(t, 'settlementDate'),
        'endDate':        get_field(t, 'endDate'),
        'contractType':   get_field(t, 'contractType'),
    })

# 只看 TXO（台指選擇權）
txo = [t for t in tickers if t['symbol'].startswith('TX')]
print(f"TXO 相關合約數: {len(txo)}")

# 顯示不同的商品前綴，了解命名規則
prefixes = sorted(set(t['symbol'][:3] for t in txo))
print(f"Symbol 前綴種類: {prefixes}")

# 找出所有不重複的 settlementDate，排序後取最近的
dates = sorted(set(t['settlementDate'] for t in txo if t['settlementDate']))
print(f"\n所有結算日（前10）: {dates[:10]}")

# 近月月選：結算日最近的一批
near_month_date = dates[0] if dates else None
print(f"目標結算日（近月）: {near_month_date}")

near_month = [t for t in txo if t['settlementDate'] == near_month_date]
calls = [t for t in near_month if 'C' in t['symbol'] or 'D' in t['symbol'].split(str(near_month_date[-2:]))[0][-3:]]
puts  = [t for t in near_month if 'P' in t['symbol']]

# 用更可靠的方法：看 name 裡有「買權」還是「賣權」
calls = [t for t in near_month if '買權' in t['name']]
puts  = [t for t in near_month if '賣權' in t['name']]

print(f"\n近月合約數: {len(near_month)}（Call: {len(calls)}, Put: {len(puts)}）")
print("前 5 個 Call:")
for t in calls[:5]:
    print(f"  {t['symbol']:20s} {t['name']:30s} 結算日:{t['settlementDate']}")
print("前 5 個 Put:")
for t in puts[:5]:
    print(f"  {t['symbol']:20s} {t['name']:30s} 結算日:{t['settlementDate']}")

# 連線數需求
total_near = len(calls) + len(puts)
ws_needed = (total_near + 199) // 200
print(f"\n近月合計: {total_near} 個，需要 {ws_needed} 條 WebSocket 連線")

# ── Step 1.3: WebSocket 訂閱測試（取前 3 個 Call）────────
print("\n=== Step 1.3: WebSocket 訂閱測試 ===")

received_messages = []
test_symbols = [t['symbol'] for t in calls[:3]]
print(f"測試訂閱: {test_symbols}")

def handle_connect(msg=None):
    print("WebSocket 已連線，開始訂閱...")
    for sym in test_symbols:
        ws.subscribe({'channel': 'trades', 'symbol': sym})
        print(f"  已訂閱: {sym}")

def handle_disconnect(*args):
    print("WebSocket 斷線")

def handle_error(*args):
    print(f"WebSocket 錯誤: {args}")

def handle_message(msg):
    received_messages.append(msg)
    # 印出原始型別與內容供診斷
    print(f"\n[推播] type={type(msg).__name__}")
    if isinstance(msg, dict):
        data = msg
    elif hasattr(msg, '__dict__'):
        data = vars(msg)
    else:
        data = {'_raw': str(msg)}

    sym   = data.get('symbol', '?')
    total = data.get('total')
    avg   = data.get('avgPrice')

    print(f"  symbol   : {sym}")
    print(f"  avgPrice : {avg}")

    if total:
        t = total if isinstance(total, dict) else vars(total)
        vol  = t.get('tradeVolume')
        bid  = t.get('totalBidMatch')
        ask  = t.get('totalAskMatch')
        print(f"  tradeVolume   : {vol}")
        print(f"  totalBidMatch : {bid}")
        print(f"  totalAskMatch : {ask}")

        # 計算淨CALL 樣本
        if bid is not None and ask is not None and (bid + ask) > 0:
            ratio = bid / (bid + ask) * 100
            net   = round((ratio - 50) / 50 * (vol or 0), 0)
            print(f"  → InOutRatio={ratio:.1f}%  淨CALL={net}")
    else:
        print(f"  [警告] total 欄位不存在，完整資料:")
        print(f"  {json.dumps(data, ensure_ascii=False, default=str)[:300]}")

sdk.init_realtime()
ws = sdk.marketdata.websocket_client.futopt
ws.on('message',    handle_message)
ws.on('connect',    handle_connect)
ws.on('disconnect', handle_disconnect)
ws.on('error',      handle_error)

ws.connect()
print("等待推播 15 秒（請確認現在是交易時段）...")
time.sleep(15)
ws.disconnect()

# ── 結果摘要 ─────────────────────────────────────────────
print(f"\n=== 結果摘要 ===")
print(f"收到推播次數: {len(received_messages)}")
if not received_messages:
    print("未收到推播（可能盤後無資料，請在交易時段 08:45~13:45 執行）")
print("\nPhase 1 完成。")
