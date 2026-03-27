"""
capital_bridge.py  ── ctypes.WinDLL 版（群益 SKCOM 2.13.x 新介面）
Windows 端橋接：群益 SKCOM → 本機 FastAPI server (main.py)

【執行環境】Windows 原生 Python 64-bit
【必要套件】pip install requests
【執行方式】
  python capital_bridge.py              # 正常執行（訂閱 TXO 近月 + 推送給 server）
  python capital_bridge.py --discover   # 列出市場 3 商品清單（確認資料格式）
  python capital_bridge.py --debug      # 印出第一個 TXO 選擇權的 struct 欄位值

【config_capital.py 範本（與此檔案同目錄）】
  SKCOM_DLL  = r"C:\\Program Files\\群益API\\SKCOM.dll"
  ID         = "your_capital_id"
  PASSWORD   = "your_capital_password"
  SERVER_URL = "http://localhost:8000"
"""

import os
import re
import sys
import math
import time
import ctypes
import queue
import logging
import datetime
import threading
from ctypes import (
    Structure, c_int, c_char_p, c_wchar_p,
    WINFUNCTYPE, POINTER, byref, create_string_buffer,
)

try:
    import requests
except ImportError:
    print("請先安裝 requests：pip install requests")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── 設定 ──────────────────────────────────────────────────────

try:
    import config_capital as cfg
except ImportError:
    print("找不到 config_capital.py，請建立（與此檔案同目錄）：")
    print('  SKCOM_DLL  = r"C:\\Program Files\\群益API\\SKCOM.dll"')
    print('  ID         = "your_capital_id"')
    print('  PASSWORD   = "your_capital_password"')
    print('  SERVER_URL = "http://localhost:8000"')
    sys.exit(1)

SKCOM_DLL    = cfg.SKCOM_DLL
SERVER_URL   = getattr(cfg, 'SERVER_URL', 'http://localhost:8000')
TARGET_NAME  = getattr(cfg, 'TARGET_NAME', '台選W403')  # 要訂閱的合約系列
MARKET_OPT   = 3  # 群益市場代號：3 = 選擇權

MODE = sys.argv[1] if len(sys.argv) > 1 else ''

# ── SKCOM Struct 定義（來自官方 SKDLLPython.py v2.13.58） ────

class _RawLOGINGW(Structure):
    """SKCenterLib_Login 新版 struct-based 登入參數"""
    _fields_ = [
        ("nAuthorityFlag", c_int),
        ("strLoginID",     c_char_p),
        ("strPassword",    c_char_p),
        ("strCustCertID",  c_char_p),
        ("strPath",        c_char_p),
    ]

class _SKSTOCKLONG2(Structure):
    """報價資料 struct（SKSTOCKLONG2）"""
    _fields_ = [
        ("nStockidx",       c_int),
        ("nDecimal",        c_int),      # 除以 10^nDecimal 得實際價格
        ("nTypeNo",         c_int),
        ("nMarketNo",       c_int),
        ("strStockNo",      c_char_p),
        ("strStockName",    c_wchar_p),
        ("strStockNoSpread",c_char_p),
        ("nOpen",           c_int),
        ("nHigh",           c_int),
        ("nLow",            c_int),
        ("nClose",          c_int),      # 現價 / 10^nDecimal
        ("nTickQty",        c_int),
        ("nRef",            c_int),      # 昨收 / 10^nDecimal
        ("nBid",            c_int),
        ("nBc",             c_int),
        ("nAsk",            c_int),
        ("nAc",             c_int),
        ("nTBc",            c_int),      # 外盤累計口數（買方主動，全日）
        ("nTAc",            c_int),      # 內盤累計口數（賣方主動，全日）
        ("nFutureOI",       c_int),
        ("nTQty",           c_int),      # 總成交量 ✅
        ("nYQty",           c_int),
        ("nUp",             c_int),
        ("nDown",           c_int),
        ("nSimulate",       c_int),
        ("nDayTrade",       c_int),
        ("nTradingDay",     c_int),
        ("nTradingLotFlag", c_int),
        ("nDealTime",       c_int),
        ("nOddLotBid",      c_int),
        ("nOddLotAsk",      c_int),
        ("nOddLotClose",    c_int),
        ("nOddLotQty",      c_int),
    ]

# ── 載入 DLL ─────────────────────────────────────────────────

if not os.path.exists(SKCOM_DLL):
    logger.error(f"找不到 SKCOM.dll：{SKCOM_DLL}")
    logger.error("請確認群益策略王安裝路徑，修改 config_bridge.py 中的 SKCOM_DLL")
    sys.exit(1)

_skcom_dir = os.path.dirname(SKCOM_DLL)
os.add_dll_directory(_skcom_dir)
logger.info(f"載入 SKCOM DLL：{SKCOM_DLL}")
_dll = ctypes.WinDLL(SKCOM_DLL)

# ── 設定所有函式簽名 ──────────────────────────────────────────

_dll.SKCenterLib_Login.argtypes        = [POINTER(_RawLOGINGW), c_char_p, c_int]
_dll.SKCenterLib_Login.restype         = c_int

_dll.SKCenterLib_GetReturnCodeMessage.argtypes = [c_int]
_dll.SKCenterLib_GetReturnCodeMessage.restype  = c_char_p

_dll.ManageServerConnection.argtypes   = [c_char_p, c_int, c_int]
_dll.ManageServerConnection.restype    = c_int

_dll.LoadCommodity.argtypes            = [c_int]
_dll.LoadCommodity.restype             = c_int

_dll.SKQuoteLib_RequestStockList.argtypes = [c_int]
_dll.SKQuoteLib_RequestStockList.restype  = c_char_p

_dll.SKQuoteLib_GetStockByStockNo.argtypes = [c_int, c_char_p, POINTER(_SKSTOCKLONG2)]
_dll.SKQuoteLib_GetStockByStockNo.restype  = c_int

_dll.SKQuoteLib_RequestStocks.argtypes = [c_char_p]
_dll.SKQuoteLib_RequestStocks.restype  = c_int

_dll.SKQuoteLib_RequestTicks.argtypes  = [c_int, c_char_p]
_dll.SKQuoteLib_RequestTicks.restype   = c_int

# ── Callback 型別（WINFUNCTYPE = stdcall，Windows DLL 標準） ──

_CONNECTION_CB_TYPE    = WINFUNCTYPE(None, c_char_p, c_int)
_REPLY_CB_TYPE         = WINFUNCTYPE(None, c_char_p, c_char_p)
_QUOTE_LONG_CB_TYPE    = WINFUNCTYPE(None, c_int, c_char_p)
# OnNotifyTicksLONG: (market_no, stock_no_ptr, ptr, date, time_hms, time_ms, bid, ask, close, qty, simulate)
_TICKS_LONG_CB_TYPE    = WINFUNCTYPE(
    None,
    c_int, c_char_p, c_int, c_int, c_int, c_int,
    c_int, c_int, c_int, c_int, c_int,
)

_dll.RegisterEventOnConnection.argtypes       = [_CONNECTION_CB_TYPE]
_dll.RegisterEventOnConnection.restype        = None
_dll.RegisterEventOnReplyMessage.argtypes     = [_REPLY_CB_TYPE]
_dll.RegisterEventOnReplyMessage.restype      = None
_dll.RegisterEventOnNotifyQuoteLONG.argtypes  = [_QUOTE_LONG_CB_TYPE]
_dll.RegisterEventOnNotifyQuoteLONG.restype   = None
_dll.RegisterEventOnNotifyTicksLONG.argtypes  = [_TICKS_LONG_CB_TYPE]
_dll.RegisterEventOnNotifyTicksLONG.restype   = None

# ── 共用狀態 ──────────────────────────────────────────────────

meta_map:  dict[str, dict] = {}
update_q:  "queue.Queue[dict]" = queue.Queue()

# ── 工具函式 ──────────────────────────────────────────────────

def _errmsg(code: int) -> str:
    """把錯誤碼轉成可讀訊息"""
    try:
        ptr = _dll.SKCenterLib_GetReturnCodeMessage(code)
        raw = ctypes.cast(ptr, ctypes.c_char_p).value
        return raw.decode('ansi', errors='replace') if raw else str(code)
    except Exception:
        return str(code)

def _get_stock(stock_no_str: str) -> "_SKSTOCKLONG2 | None":
    """呼叫 GetStockByStockNo，失敗回傳 None"""
    struct = _SKSTOCKLONG2()
    ret = _dll.SKQuoteLib_GetStockByStockNo(
        MARKET_OPT,
        stock_no_str.encode('ansi'),
        byref(struct),
    )
    return struct if ret == 0 else None

def _parse_txo(stock_no: str, stock_name: str) -> "dict | None":
    """
    判斷是否為目標系列合約（TARGET_NAME），回傳 {strike, side} 或 None。

    name 必須完全等於 TARGET_NAME + 'C' 或 TARGET_NAME + 'P'。
    code 格式：TX4{strike}C6（買權）/ TX4{strike}O6（賣權）
      不接受 C6AM / O6AM（AM結算版），避免同一履約價重複出現。
    """
    name = stock_name.strip()

    # 名稱完全比對，防止 '台選W4030C' 等誤匹配
    if name == TARGET_NAME + 'C':
        side = 'C'
    elif name == TARGET_NAME + 'P':
        side = 'P'
    else:
        return None

    # 代碼必須符合 TX4{strike}C6 或 TX4{strike}O6（不含 AM 結算後綴）
    sno = stock_no.strip().upper()
    m = re.match(r'^TX4(\d+)(C|O)6$', sno)
    if m:
        return {'strike': int(m.group(1)), 'side': side}

    return None

def _parse_stock_list(raw_bytes: bytes) -> "list[tuple[str,str,str,str]]":
    """
    解析 SKQuoteLib_RequestStockList 回傳的原始字串。
    格式：%typeNo%typeName%item1;item2;...
    每個 item：quoteCode,stockName,orderCode,expiryDate
    回傳 list of (quoteCode, stockName, orderCode, expiryDate)
    """
    decoded = raw_bytes.decode('ansi', errors='replace')
    segments = [s for s in decoded.split('%') if s]
    items = []
    i = 0
    while i + 2 < len(segments):
        try:
            int(segments[i])   # typeNo（確認是數字）
        except ValueError:
            i += 1
            continue
        raw_items = segments[i + 2]
        for entry in raw_items.split(';'):
            entry = entry.strip()
            if not entry:
                continue
            fields = entry.split(',')
            if len(fields) >= 4:
                items.append((
                    fields[0].strip(),   # quoteCode
                    fields[1].strip(),   # stockName
                    fields[2].strip(),   # orderCode
                    fields[3].strip(),   # expiryDate
                ))
        i += 3
    return items

# ── HTTP 推送（背景 worker） ───────────────────────────────────

def _http_worker():
    """
    每 0.5 秒把 queue 裡的更新批次 POST /api/feed。
    同一合約只保留最新一筆，避免 callback thread 阻塞。
    """
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
            r = requests.post(
                f"{SERVER_URL}/api/feed",
                json=payload,
                timeout=3,
            )
            if r.status_code != 200:
                logger.warning(f"POST /api/feed HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"POST /api/feed 失敗：{e}")

# ── 定期輪詢（補漏回調）─────────────────────────────────────

def _do_resubscribe():
    """重新呼叫 RequestStocks，強制 SKCOM 推送所有合約當前狀態。"""
    codes = list(meta_map.keys())
    batch_size = 200
    for b_start in range(0, len(codes), batch_size):
        chunk   = codes[b_start:b_start + batch_size]
        sym_str = ','.join(chunk)
        try:
            ret = _dll.SKQuoteLib_RequestStocks(sym_str.encode('ansi'))
            if ret != 0:
                logger.warning(f"重新訂閱失敗 [{ret}]：{_errmsg(ret)}")
        except Exception as e:
            logger.warning(f"重新訂閱例外：{e}")
    logger.info("_do_resubscribe 完成")

def _poll_worker():
    """
    固定每 5 秒強制 RequestStocks，讓 SKCOM 推送全量最新快照。
    首次執行等待 10 秒讓訂閱穩定。
    """
    time.sleep(10)
    while True:
        _do_resubscribe()
        time.sleep(0.5)

# ── 載入 + 訂閱（在背景 thread 執行） ────────────────────────

def _load_and_subscribe():
    """
    連線成功後：
    1. LoadCommodity(3)
    2. RequestStockList(3) → 解析 TXO 近月
    3. 逐筆 GetStockByStockNo 取 nRef / nDecimal
    4. POST /api/init
    5. RequestStocks 訂閱
    """
    logger.info("載入選擇權商品資料（LoadCommodity）...")
    ret = _dll.LoadCommodity(MARKET_OPT)
    if ret != 0:
        logger.error(f"LoadCommodity 失敗 [{ret}]：{_errmsg(ret)}")
        return

    logger.info("取得商品清單（RequestStockList）...")
    all_items = []
    for attempt in range(3):
        ptr = _dll.SKQuoteLib_RequestStockList(MARKET_OPT)
        raw = ctypes.cast(ptr, ctypes.c_char_p).value
        if not raw:
            logger.warning(f"RequestStockList 回傳空值（第 {attempt+1} 次），等待重試...")
            time.sleep(2)
            continue
        all_items = _parse_stock_list(raw)
        if all_items:
            break
        logger.warning(f"RequestStockList 解析到 0 個商品（第 {attempt+1} 次），等待重試...")
        time.sleep(2)
    if not all_items:
        logger.error("RequestStockList 三次均回傳空清單，請稍後重啟 bridge")
        return
    logger.info(f"市場 3 共 {len(all_items)} 個商品，篩選 TXO...")

    if MODE == '--discover':
        _do_discover(all_items)
        return

    # 找出所有 TXO 選擇權
    all_meta = []
    for quote_code, stock_name, order_code, expiry_date in all_items:
        m = _parse_txo(quote_code, stock_name)
        if m:
            all_meta.append((quote_code, stock_name, expiry_date, m))

    if not all_meta:
        logger.error("找不到任何 TXO 選擇權！請改用 --discover 確認資料格式")
        return

    # 近月 = 最早結算日
    dates     = sorted(set(x[2] for x in all_meta if x[2]))
    near_date = dates[0] if dates else 'unknown'
    near      = [x for x in all_meta if x[2] == near_date]
    logger.info(f"近月結算日：{near_date}，共 {len(near)} 個合約")

    if MODE == '--debug':
        _do_debug(near)
        return

    # 逐筆查 nRef / nDecimal（只取 prev_close，快照等訂閱後再做）
    contracts = []
    sym_list  = []
    for code, name, exp, m in near:
        s = _get_stock(code)
        if s:
            divisor    = math.pow(10, s.nDecimal) if s.nDecimal > 0 else 1.0
            prev_close = s.nRef / divisor
        else:
            prev_close = 0.0
        contracts.append({
            'symbol':     code,
            'strike':     m['strike'],
            'side':       m['side'],
            'prev_close': prev_close,
        })
        meta_map[code] = m
        sym_list.append(code)

    # POST /api/init
    try:
        r = requests.post(
            f"{SERVER_URL}/api/init",
            json={'settlement_date': near_date, 'contracts': contracts},
            timeout=10,
        )
        logger.info(f"POST /api/init → HTTP {r.status_code}：{r.text[:80]}")
    except Exception as e:
        logger.error(f"POST /api/init 失敗：{e}")
        return

    # 訂閱報價（每批 ≤ 200）
    batch_size = 200
    for start in range(0, len(sym_list), batch_size):
        chunk   = sym_list[start:start + batch_size]
        sym_str = ','.join(chunk)
        ret     = _dll.SKQuoteLib_RequestStocks(sym_str.encode('ansi'))
        if ret != 0:
            logger.error(f"SKQuoteLib_RequestStocks 失敗 [{ret}]：{_errmsg(ret)}")
        else:
            logger.info(f"已訂閱 {start}~{start + len(chunk) - 1}")

    logger.info(f"訂閱完成，共 {len(sym_list)} 個 TXO 合約，等待即時報價...")

    # 等 DLL 刷新訂閱後的行情資料，再做初始快照
    time.sleep(3)
    logger.info("推送初始快照（訂閱後）...")
    snapshot = []
    debug_rows = []  # (symbol, nTAc, nTBc, nTQty, price) 供 log 用
    for code in sym_list:
        s = _get_stock(code)
        if not s:
            continue
        divisor = math.pow(10, s.nDecimal) if s.nDecimal > 0 else 1.0
        snapshot.append({
            'symbol':       code,
            'bid_match':    s.nTBc,    # 外盤口數（買方主動，全日累計）
            'ask_match':    s.nTAc,    # 內盤口數（賣方主動，全日累計）
            'trade_volume': s.nTQty,   # 全日累計成交量
            'avg_price':    s.nClose / divisor,
        })
        debug_rows.append((code, s.nTAc, s.nTBc, s.nTQty, s.nClose / divisor))
    if snapshot:
        for code, tac, tbc, tqty, price in debug_rows[:5]:
            logger.info(
                f"  快照樣本 {code}: "
                f"nTAc={tac} nTBc={tbc} nTAc+nTBc={tac+tbc} "
                f"nTQty={tqty} price={price:.2f}"
            )
        # 額外印出 33050C（比對目標），合約代碼含 33050 且為 C（買權）
        for code, tac, tbc, tqty, price in debug_rows:
            if re.search(r'33050C', code, re.IGNORECASE):
                logger.info(
                    f"  [TARGET 33050C] {code}: "
                    f"nTAc={tac} nTBc={tbc} nTAc+nTBc={tac+tbc} "
                    f"nTQty={tqty} price={price:.2f}"
                )
                break
        try:
            r = requests.post(f"{SERVER_URL}/api/feed", json=snapshot, timeout=15)
            logger.info(f"初始快照 → HTTP {r.status_code}，{len(snapshot)} 筆")
        except Exception as e:
            logger.error(f"初始快照推送失敗：{e}")

# ── 模式：--discover ──────────────────────────────────────────

def _do_discover(items: list):
    logger.info("=== --discover：搜尋 '台選W403' ===")
    for code, name, order_code, expiry in items:
        if '台選W403' in name:
            logger.info(f"  code={code!r:25s}  name={name!r:35s}  order={order_code!r:25s}  expiry={expiry!r}")
    logger.info(f"（共 {len(items)} 筆）")

# ── 模式：--debug ─────────────────────────────────────────────

def _do_debug(near: list):
    logger.info("=== --debug：第一個近月 TXO 合約的 struct 欄位 ===")
    if not near:
        logger.info("無近月合約可顯示")
        return
    code, name, exp, m = near[0]
    logger.info(f"合約：{code}（{name}），到期：{exp}，解析：{m}")
    s = _get_stock(code)
    if not s:
        logger.info("GetStockByStockNo 失敗")
        return
    for field_name, _ in _SKSTOCKLONG2._fields_:
        val = getattr(s, field_name)
        if isinstance(val, bytes):
            val = val.decode('ansi', errors='replace')
        logger.info(f"  {field_name:20s} = {val!r}")

# ── SKCOM 事件 Callbacks ──────────────────────────────────────

def _on_connection(login_id_ptr, code):
    """
    ManageServerConnection 觸發的連線事件。
    code 與舊 COM nKind 相同：3001=連線, 3002=斷線, 3003=商品資料就緒
    """
    login_id = login_id_ptr.decode('ansi') if login_id_ptr else ''
    logger.info(f"OnConnection: user={login_id}, code={code} ({_errmsg(code)})")

    SK_STOCKS_READY = 3003
    SK_DISCONNECTED = 3002

    if code == SK_STOCKS_READY:
        # 商品資料就緒 → 背景載入商品 + 訂閱
        threading.Thread(target=_load_and_subscribe, daemon=True).start()
    elif code == SK_DISCONNECTED:
        logger.warning("報價伺服器斷線，等待自動重連...")

def _on_reply_message(msg1_ptr, msg2_ptr):
    msg1 = msg1_ptr.decode('ansi') if msg1_ptr else ''
    msg2 = msg2_ptr.decode('ansi') if msg2_ptr else ''
    logger.debug(f"OnReplyMessage: {msg1} / {msg2}")

_unknown_market_log_count = 0

def _on_notify_quote_long(market_no, stock_no_ptr):
    """
    有報價更新時觸發（已訂閱的合約）。
    立刻呼叫 GetStockByStockNo 讀取最新 nTBc/nTAc/nTQty，丟入 queue。
    注意：不再以 market_no 過濾，改用 meta_map 成員身份作為唯一判斷。
    夜盤期間 SKCOM 可能對 PUT 合約送出不同的 market_no（如 7），
    若在此過濾則 PUT 永遠不更新。
    """
    global _unknown_market_log_count
    try:
        stock_no = stock_no_ptr.decode('ansi') if stock_no_ptr else ''
        if stock_no not in meta_map:
            # 偶爾記錄非我們合約的 market_no，協助診斷
            if market_no != MARKET_OPT and _unknown_market_log_count < 20:
                _unknown_market_log_count += 1
                logger.debug(f"非訂閱合約 callback: market_no={market_no} stock_no={stock_no!r}")
            return
        if market_no != MARKET_OPT:
            logger.info(f"PUT/CALL 夜盤 callback: market_no={market_no}（非3）stock_no={stock_no}")

        s = _get_stock(stock_no)
        if not s:
            return

        divisor = math.pow(10, s.nDecimal) if s.nDecimal > 0 else 1.0
        logger.info(f"TICK {stock_no}: nTBc={s.nTBc}(外盤) nTAc={s.nTAc}(內盤) nTQty={s.nTQty} close={s.nClose/divisor:.2f}")
        update_q.put({
            'symbol':       stock_no,
            'bid_match':    s.nTBc,    # 外盤口數（買方主動，全日累計）
            'ask_match':    s.nTAc,    # 內盤口數（賣方主動，全日累計）
            'trade_volume': s.nTQty,   # 全日累計成交量
            'avg_price':    s.nClose / divisor,
        })
    except Exception as e:
        logger.warning(f"OnNotifyQuoteLONG error：{e}")

def _on_notify_ticks_long(
    market_no, stock_no_ptr,
    ptr, date, time_hms, time_ms,
    bid, ask, close, qty, simulate,
):
    """
    每筆實時成交觸發（OnNotifyTicksLONG）。
    直接從 callback 參數取得 close/qty，再呼叫 GetStockByStockNo 補全累計欄位。
    """
    try:
        if simulate:   # 試撮不算
            return
        stock_no = stock_no_ptr.decode('ansi') if stock_no_ptr else ''
        if stock_no not in meta_map:
            return

        s = _get_stock(stock_no)
        if not s:
            return

        divisor = math.pow(10, s.nDecimal) if s.nDecimal > 0 else 1.0
        price   = close / divisor if divisor else 0.0
        logger.info(
            f"TICK_T {stock_no}: close={price:.2f} qty={qty} "
            f"nTAc={s.nTAc} nTBc={s.nTBc} nTQty={s.nTQty}"
        )
        update_q.put({
            'symbol':       stock_no,
            'bid_match':    s.nTBc,    # 外盤口數（買方主動，全日累計）
            'ask_match':    s.nTAc,    # 內盤口數（賣方主動，全日累計）
            'trade_volume': s.nTQty,
            'avg_price':    price,
        })
    except Exception as e:
        logger.warning(f"OnNotifyTicksLONG error：{e}")

# ── 自動重新初始化排程 ────────────────────────────────────────

# 台灣期貨市場盤前重新初始化時間（hour, minute）
_REINIT_TIMES = {(8, 43), (14, 58)}   # 日盤 08:45 前、夜盤 15:00 前
_last_reinit_key = ""                  # "YYYYMMDD-HH" 防止同小時重複觸發

def _auto_reinit_scheduler():
    """
    每 20 秒檢查一次。到達盤前時間時，呼叫 _load_and_subscribe()：
    - 重新取得近月合約（可能已換週）
    - POST /api/init → server store.clear()
    - 重新訂閱 SKCOM
    確保跨盤（日→夜、夜→日）時舊資料被清空。
    """
    global _last_reinit_key
    while True:
        time.sleep(20)
        now = datetime.datetime.now()
        if (now.hour, now.minute) not in _REINIT_TIMES:
            continue
        key = f"{now.strftime('%Y%m%d')}-{now.hour}"
        if key == _last_reinit_key:
            continue
        _last_reinit_key = key
        logger.info(
            f"[排程] {now.strftime('%H:%M')} 盤前重新初始化"
            f"（清空 store、重新訂閱）..."
        )
        threading.Thread(target=_load_and_subscribe, daemon=True).start()

# ── 主程式 ────────────────────────────────────────────────────

def main():
    # 保存 callback 物件，避免被 GC 回收導致 crash
    _conn_cb  = _CONNECTION_CB_TYPE(_on_connection)
    _reply_cb = _REPLY_CB_TYPE(_on_reply_message)
    _quote_cb = _QUOTE_LONG_CB_TYPE(_on_notify_quote_long)
    _ticks_cb = _TICKS_LONG_CB_TYPE(_on_notify_ticks_long)

    # 先掛事件（必須在 Login 之前）
    _dll.RegisterEventOnReplyMessage(_reply_cb)
    _dll.RegisterEventOnConnection(_conn_cb)
    _dll.RegisterEventOnNotifyQuoteLONG(_quote_cb)
    _dll.RegisterEventOnNotifyTicksLONG(_ticks_cb)

    # 登入（struct-based，新版 API）
    logger.info(f"登入群益 ({cfg.ID})...")
    login_struct = _RawLOGINGW(
        nAuthorityFlag = 0,
        strLoginID     = cfg.ID.encode('utf-8'),
        strPassword    = cfg.PASSWORD.encode('utf-8'),
        strCustCertID  = None,
        strPath        = None,
    )
    ACCOUNT_BUF_SIZE = 4096
    account_buf = create_string_buffer(ACCOUNT_BUF_SIZE)
    code = _dll.SKCenterLib_Login(byref(login_struct), account_buf, ACCOUNT_BUF_SIZE)
    if code != 0:
        logger.error(f"登入失敗 [{code}]：{_errmsg(code)}")
        return
    logger.info("登入成功")

    # 連線報價伺服器（status=0=連線, target=1=國內行情）
    logger.info("連線國內報價伺服器...")
    ret = _dll.ManageServerConnection(cfg.ID.encode('utf-8'), 0, 1)
    if ret != 0:
        logger.error(f"ManageServerConnection 失敗 [{ret}]：{_errmsg(ret)}")
        return

    # 啟動 HTTP 推送 worker + 定期輪詢 worker（--discover/--debug 模式不需要）
    if MODE not in ('--discover', '--debug'):
        threading.Thread(target=_http_worker,          daemon=True).start()
        threading.Thread(target=_poll_worker,          daemon=True).start()
        threading.Thread(target=_auto_reinit_scheduler, daemon=True).start()

    logger.info("等待連線事件（OnConnection）...")
    # SKCOM 的行情 callback 透過 Windows 訊息佇列派送，
    # 需要訊息泵才能觸發 OnNotifyQuoteLONG。
    try:
        from ctypes import wintypes
        msg = wintypes.MSG()
        while True:
            # PeekMessage(PM_REMOVE=1)：非阻塞取出訊息並處理
            if ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == 0x0012:  # WM_QUIT
                    break
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        logger.info("停止")


if __name__ == '__main__':
    main()
