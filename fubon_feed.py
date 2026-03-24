"""
fubon_feed.py  ── fubon_neo SDK（富邦期貨 API）
Windows 端橋接：富邦行情 → 本機 FastAPI server (main.py)

【執行環境】Windows 原生 Python 64-bit
【執行方式】
  python fubon_feed.py        # 正常執行（TX4 週選）
  python fubon_feed.py TX4    # 明確指定 W4 週選
  python fubon_feed.py TXO    # 月選

【合約系列對照】
  TX4 = 台選W4（第4週週選）  TX1 = W1  TX2 = W2  TX5 = W5  TXO = 月選

【Symbol 格式】
  TX4{strike}C6  → 買權（Call）
  TX4{strike}O6  → 賣權（Put）
  例：TX432500C6 = TX4 系列，履約價 32500，買權

【行情取得】
  WebSocket futopt aggregates channel（Normal mode）
  → total.totalBidMatch / totalAskMatch / tradeVolume / closePrice
  夜盤基準：REST quote(session='afterhours') 啟動時取一次，疊加到日盤數值

【config_fubon.py 欄位】
  ID, PASSWORD, CERT_PATH, CERT_PASSWORD, SERVER_URL
"""

import re
import sys
import time
import queue
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    print("請先安裝 requests：pip install requests")
    sys.exit(1)

try:
    import orjson
except ImportError:
    import json as orjson

try:
    from fubon_neo.sdk import FubonSDK
    from fubon_neo.adapter import Mode
except ImportError:
    print("找不到 fubon_neo，請先安裝 SDK whl")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── 設定 ──────────────────────────────────────────────────────

try:
    import config_fubon as cfg
except ImportError:
    print("找不到 config_fubon.py")
    sys.exit(1)

SERVER_URL    = getattr(cfg, 'SERVER_URL', 'http://localhost:8000')
TARGET_SERIES = sys.argv[1] if len(sys.argv) > 1 else 'TX4'
# 用法：python fubon_bridge.py TX4   （W4週選，預設）
#       python fubon_bridge.py TXO   （月選）
#       python fubon_bridge.py TX1   （W1週選）

# ── 共用狀態 ──────────────────────────────────────────────────

meta_map:  dict[str, dict] = {}
update_q:  "queue.Queue[dict]" = queue.Queue()
_baseline: dict[str, dict] = {}   # 夜盤基準值（啟動時取一次，固定不變）
_debug_msg_count = 0

# ── Symbol 解析 ────────────────────────────────────────────────

# TX4{strike}C6 → Call / TX4{strike}O6 → Put
_SYM_RE = re.compile(r'^(TX[145O]|TXO)(\d+)(C|O)6$')

def _parse_sym(symbol: str) -> "dict | None":
    """
    回傳 {strike, side} 或 None。
    C6 後綴 = Call，O6 後綴 = Put（O 不是 P）。
    """
    m = _SYM_RE.match(symbol)
    if not m:
        return None
    series = m.group(1)
    if series != TARGET_SERIES:
        return None
    side = 'C' if m.group(3) == 'C' else 'P'
    return {'strike': int(m.group(2)), 'side': side}

# ── HTTP 推送（背景 worker） ───────────────────────────────────

def _http_worker():
    batch: dict[str, dict] = {}
    while True:
        deadline = time.time() + 0.5
        while time.time() < deadline:
            try:
                item = update_q.get(timeout=max(0.01, deadline - time.time()))
                batch[item['symbol']] = item
            except queue.Empty:
                break
        if not batch:
            continue
        payload = list(batch.values())
        batch.clear()
        try:
            r = requests.post(f"{SERVER_URL}/api/feed", json=payload, timeout=3)
            if r.status_code != 200:
                logger.warning(f"POST /api/feed HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"POST /api/feed 失敗：{e}")

# ── WebSocket 訊息處理 ─────────────────────────────────────────

def _on_message(raw_data):
    """
    aggregates channel 回傳（raw bytes）。
    前 3 筆印原始 JSON 確認欄位名稱。
    """
    global _debug_msg_count
    try:
        msg = orjson.loads(raw_data) if isinstance(raw_data, (bytes, str)) else raw_data
        if not isinstance(msg, dict):
            return

        if _debug_msg_count < 3:
            _debug_msg_count += 1
            logger.info(f"[DEBUG raw #{_debug_msg_count}] {msg}")

        if msg.get('event') not in ('data', 'snapshot'):
            return

        data   = msg.get('data', {})
        symbol = data.get('symbol', '')
        if symbol not in meta_map:
            return

        total        = data.get('total', {})
        bid_match    = int(total.get('totalBidMatch') or 0)
        ask_match    = int(total.get('totalAskMatch') or 0)
        trade_volume = int(total.get('tradeVolume') or 0)
        avg_price    = float(
            data.get('closePrice') or
            data.get('lastPrice')  or
            data.get('price')      or 0.0
        )

        base = _baseline.get(symbol, {})
        combined_bid  = bid_match    + base.get('bid_match', 0)
        combined_ask  = ask_match    + base.get('ask_match', 0)
        combined_vol  = trade_volume + base.get('trade_volume', 0)

        logger.debug(
            f"AGG {symbol}: "
            f"bid={bid_match}+{base.get('bid_match',0)} "
            f"ask={ask_match}+{base.get('ask_match',0)} "
            f"vol={trade_volume}+{base.get('trade_volume',0)} "
            f"price={avg_price}"
        )
        update_q.put({
            'symbol':          symbol,
            'bid_match':       combined_bid,
            'ask_match':       combined_ask,
            'trade_volume':    combined_vol,
            'bid_match_day':   bid_match,    # 純日盤
            'ask_match_day':   ask_match,
            'trade_volume_day': trade_volume,
            'avg_price':       avg_price,
        })
    except Exception as e:
        logger.warning(f"_on_message error：{e}")

# ── 主程式 ────────────────────────────────────────────────────

def main():
    # 1. 登入
    sdk = FubonSDK()
    logger.info(f"登入富邦期貨 ({cfg.ID})...")
    try:
        accounts = sdk.login(cfg.ID, cfg.PASSWORD, cfg.CERT_PATH, cfg.CERT_PASSWORD)
        logger.info(f"登入成功：{accounts}")
    except Exception as e:
        logger.error(f"登入失敗：{e}")
        sys.exit(1)

    # 2. 初始化行情（Normal mode = aggregates channel 可用）
    logger.info("初始化行情連線（Normal mode）...")
    sdk.init_realtime(mode=Mode.Normal)

    # 3. 取得所有期選合約（tickers，含個別履約價）
    logger.info(f"取得合約清單（tickers，篩選 {TARGET_SERIES}）...")
    try:
        resp = sdk.marketdata.rest_client.futopt.intraday.tickers(
            type='OPTION', exchange='TAIFEX', symbol='TXO'
        )
        all_data = resp.get('data', []) if isinstance(resp, dict) else (resp or [])
    except Exception as e:
        logger.error(f"取得合約清單失敗：{e}")
        sys.exit(1)

    logger.info(f"收到 {len(all_data)} 個合約，篩選 {TARGET_SERIES}...")

    # 篩選目標系列
    target = []
    for p in all_data:
        m = _parse_sym(p.get('symbol', ''))
        if m:
            target.append((p, m))

    if not target:
        logger.error(f"找不到 {TARGET_SERIES} 合約！請確認 config_fubon.py 的 TARGET_SERIES")
        sys.exit(1)

    # 近月 = 最早 settlementDate
    dates = sorted(set(p.get('settlementDate', '') for p, _ in target if p.get('settlementDate')))
    near_date = dates[0]
    near = [(p, m) for p, m in target if p.get('settlementDate') == near_date]
    logger.info(f"結算日：{near_date}，共 {len(near)} 個合約")

    # 4. 取夜盤基準值（平行拉取，疊加到日盤）
    logger.info("取得夜盤基準值（afterhours）...")
    rc = sdk.marketdata.rest_client.futopt.intraday

    def _fetch_ah(symbol: str) -> tuple[str, dict]:
        try:
            ah = rc.quote(symbol=symbol, session='afterhours')
            t = ah.get('total', {})
            return symbol, {
                'bid_match':    int(t.get('totalBidMatch')  or 0),
                'ask_match':    int(t.get('totalAskMatch')  or 0),
                'trade_volume': int(t.get('tradeVolume')    or 0),
            }
        except Exception:
            return symbol, {'bid_match': 0, 'ask_match': 0, 'trade_volume': 0}

    with ThreadPoolExecutor(max_workers=10) as exe:
        futures = {exe.submit(_fetch_ah, p['symbol']): p['symbol'] for p, _ in near}
        for f in as_completed(futures):
            sym, data = f.result()
            _baseline[sym] = data

    ah_nonzero = sum(1 for v in _baseline.values() if v['trade_volume'] > 0)
    logger.info(f"夜盤基準值完成：{len(_baseline)} 個合約，{ah_nonzero} 個有夜盤成交")

    # 5. 建立 meta_map + contracts
    contracts = []
    for p, m in near:
        symbol     = p['symbol']
        prev_close = float(p.get('referencePrice') or 0)
        meta_map[symbol] = m
        contracts.append({
            'symbol':     symbol,
            'strike':     m['strike'],
            'side':       m['side'],
            'prev_close': prev_close,
        })

    # 6. POST /api/init
    settlement_str = near_date.replace('-', '')
    try:
        r = requests.post(
            f"{SERVER_URL}/api/init",
            json={'settlement_date': settlement_str, 'contracts': contracts},
            timeout=10,
        )
        logger.info(f"POST /api/init → HTTP {r.status_code}：{r.text[:80]}")
    except Exception as e:
        logger.error(f"POST /api/init 失敗：{e}")
        sys.exit(1)

    # 6. 啟動 HTTP worker
    threading.Thread(target=_http_worker, daemon=True).start()

    # 7. WebSocket 訂閱 aggregates
    logger.info("連接 WebSocket（futopt）...")
    futopt_ws = sdk.marketdata.websocket_client.futopt
    futopt_ws.on('message',    _on_message)
    futopt_ws.on('error',      lambda e: logger.error(f"WS error：{e}"))
    futopt_ws.on('disconnect', lambda *_: logger.warning("WS 斷線"))

    futopt_ws.connect()
    logger.info(f"已連接，訂閱 {len(near)} 個合約的 aggregates channel...")

    for p, _ in near:
        futopt_ws.subscribe({'channel': 'aggregates', 'symbol': p['symbol']})

    logger.info("訂閱完成，等待即時報價...")

    # 8. 主迴圈
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("停止")
        futopt_ws.disconnect()


if __name__ == '__main__':
    main()
