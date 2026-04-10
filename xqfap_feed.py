"""
xqfap_feed.py — 新富邦e01 DDE 橋接 (v2.28)
讀取 XQFAP DDE server 的 InOutRatio + TotalVolume + AvgPrice
推送至 FastAPI server (main.py) /api/init + /api/feed

【架構】
  pywin32 DDE  → 探索合約 Name（啟動時）
  DDEML ctypes → TotalVolume advise（push）+ InOutRatio/AvgPrice REQUEST（on-demand）

【DDE Advise 架構（v2.28）】
  啟動時：一次性探索所有系列 → 對全部合約訂閱 TF-TotalVolume ADVSTART
  主執行緒：GetMessageW blocking loop + advise callback
  callback：TotalVolume 有變動 → put 進 _change_queue
  advise_worker：每 0.5s 批次 REQUEST InOutRatio/AvgPrice → POST /api/feed

【執行方式】
  python xqfap_feed.py              # 正常執行
  python xqfap_feed.py --discover   # 列出本月所有可用系列碼
"""

import sys
import os
import time
import ctypes
import logging
import logging.handlers
import threading
import datetime
import queue
import atexit
import traceback
import ctypes.wintypes
from concurrent.futures import ThreadPoolExecutor, as_completed

import win32ui  # noqa: F401 — dde 依賴它
import dde

try:
    import requests
except ImportError:
    print("請先安裝 requests：pip install requests")
    sys.exit(1)

SERVER_URL = "http://localhost:8000"
_http = requests.Session()   # 連線重用（keep-alive），所有 thread 共用

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'monitor')
os.makedirs(_log_dir, exist_ok=True)
_fh = logging.handlers.RotatingFileHandler(
    os.path.join(_log_dir, 'xqfap.log'),
    maxBytes=10 * 1024 * 1024, backupCount=3, encoding='utf-8',
)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(_fh)
logger = logging.getLogger(__name__)

# ── 設定 ──────────────────────────────────────────────────────


STRIKE_STEP = 50
MISS_LIMIT  = 10  # 連續10筆miss（=500點空洞）即停；遠端OTM履約價間距可能>250點

MODE = sys.argv[1] if len(sys.argv) > 1 else ''

# ── pywin32 DDE（探索 + InOutRatio / TotalVolume）─────────────

_srv  = None
_conv = None


def _connect_dde() -> bool:
    global _srv, _conv
    try:
        _srv = dde.CreateServer()
        _srv.Create("XQFAPFeed")
        _conv = dde.CreateConversation(_srv)
        _conv.ConnectTo("XQFAP", "Quote")
        logger.info("XQFAP DDE 連線成功（pywin32）")
        return True
    except Exception as e:
        logger.error(f"XQFAP DDE 連線失敗：{e}，請確認新富邦e01已開啟")
        return False


def _req(item: str) -> str:
    """pywin32 DDE request；失敗或 '-' 回傳空字串"""
    try:
        val = _conv.Request(item)
        if val is None:
            return ''
        val = str(val).strip()
        return '' if val == '-' else val
    except Exception:
        return ''


def _to_float(s: str) -> float:
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return 0.0


# ── DDEML ctypes（僅用於 AvgPrice，修正小數截斷）─────────────

_user32 = ctypes.WinDLL("user32")

_PFNCALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
)
_DWORD = ctypes.c_ulong

_CF_TEXT          = 1
_XTYP_REQUEST     = 0x20B0
_DDE_TIMEOUT_MS   = 5000
_CP_WINUNICODE    = 1200
_APPCMD_CLIENTONLY = 0x0010
_XTYP_ADVSTART     = 0x1030     # 0x0030 | XCLASS_BOOL（舊版常數，daqFAP 需要）
_XTYP_ADVDATA      = 0x4010     # 0x0010 | XCLASS_FLAGS
_TIMEOUT_ASYNC     = 0xFFFFFFFF  # ADVSTART 必須用非同步模式
_DDE_FACK          = 0x8000
_WM_APP_REINIT       = 0x8001   # PostThreadMessageW：觸發重新初始化
_WM_APP_SUBSCRIBE    = 0x8002   # PostThreadMessageW：訂閱新合約 advise
_WM_APP_SWITCH       = 0x8003   # PostThreadMessageW：切換 active series
_WM_TIMER            = 0x0113   # Windows WM_TIMER
_WATCHDOG_TIMER_ID   = 1        # SetTimer ID
_WATCHDOG_INTERVAL   = 30000    # 30 秒觸發一次 WM_TIMER（ms）
_WATCHDOG_THRESHOLD  = 60.0     # 超過 60 秒無 callback → 重新連線
_RESUBSCRIBE_COOLDOWN = 120.0   # 重新訂閱最短冷卻時間（秒）
_XTYP_ADVSTOP        = 0x8040   # 0x0040 | XCLASS_NOTIFICATION
_BG_POLL_INTERVAL    = 20       # 背景系列輪詢間隔（秒）
_SWITCH_LISTENER_PORT = 8001    # main.py 切換系列時的 TCP 通知 port
_BULK_REQ_THREADS    = 4        # bulk_request_series 並行 DDEML 連線數
_bulk_req_sem        = threading.Semaphore(1)  # 限制同時只跑 1 個 bulk_req，防止 DDE 競爭
_ADVISE_REQ_THREADS  = 4        # advise_worker 平行 DDE request threads
_QUOTE_POLL_THREADS  = 24       # 輪詢用 DDEML 連線數（6 欄位 × 248 合約，高並行）

# 64-bit Windows：handle 是 64-bit pointer。
# restype / argtypes 兩端都必須宣告，否則 ctypes 預設 c_int（32-bit）截斷。
_user32.DdeCreateStringHandleW.restype  = ctypes.c_void_p
_user32.DdeCreateStringHandleW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_int]
_user32.DdeFreeStringHandle.argtypes    = [ctypes.c_ulong, ctypes.c_void_p]
_user32.DdeConnect.restype              = ctypes.c_void_p
_user32.DdeConnect.argtypes             = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_user32.DdeClientTransaction.restype    = ctypes.c_void_p
_user32.DdeClientTransaction.argtypes  = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
]
_user32.DdeGetData.argtypes             = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong]
_user32.DdeFreeDataHandle.argtypes      = [ctypes.c_void_p]
_user32.DdeQueryStringW.argtypes        = [ctypes.c_ulong, ctypes.c_void_p,
                                            ctypes.c_char_p, ctypes.c_ulong, ctypes.c_int]
_user32.DdeQueryStringW.restype         = ctypes.c_ulong
_user32.DdeDisconnect.argtypes          = [ctypes.c_void_p]
_user32.DdeUninitialize.argtypes        = [ctypes.c_ulong]
_user32.GetMessageW.argtypes            = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_uint, ctypes.c_uint]
_user32.GetMessageW.restype             = ctypes.c_int   # -1=error, 0=WM_QUIT, >0=msg
_user32.TranslateMessage.argtypes       = [ctypes.c_void_p]
_user32.DispatchMessageW.argtypes       = [ctypes.c_void_p]
_user32.PostThreadMessageW.argtypes     = [ctypes.c_ulong, ctypes.c_uint,
                                            ctypes.c_size_t, ctypes.c_size_t]
_user32.PostThreadMessageW.restype      = ctypes.c_bool
_user32.SetTimer.argtypes               = [ctypes.c_void_p, ctypes.c_size_t,
                                            ctypes.c_uint, ctypes.c_void_p]
_user32.SetTimer.restype                = ctypes.c_size_t
_user32.KillTimer.argtypes              = [ctypes.c_void_p, ctypes.c_size_t]


def _null_cb(a, b, c, d, e, f, g, h):
    """Worker thread 用空 callback（只做 REQUEST，不接收 advise）"""
    return None


def _advise_cb_fn(wType, uFmt, hconv, hsz1, hsz2, hdata, dw1, dw2):
    """主執行緒 advise callback：處理 XTYP_ADVDATA TF-TotalVolume"""
    if wType != _XTYP_ADVDATA:
        return 0
    # 取 item 名稱（hsz2 → "TXYN03C33000.TF-TotalVolume"）
    _user32.DdeQueryStringW(_ddeml_inst.value, hsz2, _advise_cb_buf, 256, 1004)   # 1004=CP_WINANSI
    item_full = _advise_cb_buf.value.decode('cp950', errors='replace').strip()
    if '.' not in item_full:
        return _DDE_FACK
    symbol, field = item_full.rsplit('.', 1)
    if field != 'TF-TotalVolume':
        return _DDE_FACK
    # 取值（用 DdeGetData；split \x01 去掉垃圾後綴）
    sz = _user32.DdeGetData(hdata, None, 0, 0)
    if sz <= 0:
        return _DDE_FACK
    data_buf = ctypes.create_string_buffer(sz)
    _user32.DdeGetData(hdata, data_buf, sz, 0)
    val_str = (data_buf.raw.rstrip(b'\x00')
               .split(b'\x01')[0]
               .decode('cp950', errors='replace').strip())
    try:
        new_vol = int(float(val_str))
    except (ValueError, TypeError):
        return _DDE_FACK
    global _last_callback_time, _last_callback_by_symbol
    _now_ts = time.time()
    _last_callback_time = _now_ts
    _last_callback_by_symbol[symbol] = _now_ts
    series = _sym_to_series.get(symbol)
    if series:
        try:
            _change_queue.put_nowait((series, symbol, new_vol))
        except queue.Full:
            pass
    return _DDE_FACK


_dde_callback         = _PFNCALLBACK(_advise_cb_fn)
_ddeml_inst           = _DWORD(0)
_advise_cb_buf        = ctypes.create_string_buffer(256)  # 重複使用，避免高頻 callback 配置
_ddeml_hconv          = None
_change_queue         = queue.Queue(maxsize=10000)
_sym_to_series: dict  = {}   # full-series symbol → series（e.g. 'TXYN03C33000' → 'TXYN03'）
_advise_loop_tid      = 0    # Win32 thread ID of advise message loop
_pending_subscribe: set = set()  # 用 set 去重，避免重複 ADVSTART
_pending_subscribe_lock   = threading.Lock()
_last_callback_time: float = 0.0   # 最後收到 ADVDATA 的時間（全域）
_last_callback_by_symbol: dict = {}  # symbol → 最後 callback 時間（觀測 partial failure 用）
_quote_prevs: dict = {}            # symbol → (bid, ask, last)，供 quote_poll_worker 去重
_last_resubscribe_time: float = 0.0  # 最後重新訂閱的時間
_active_advise_series: str  = ''     # 目前持有 advise 的 full series
_pending_switch_series: str = ''     # 待切換的 full series（series_watcher → advise_loop）
_pending_switch_lock        = threading.Lock()


def _connect_ddeml() -> bool:
    """建立 DDEML 連線（advise 訂閱 + AvgPrice REQUEST 共用）"""
    global _ddeml_inst, _ddeml_hconv
    try:
        ret = _user32.DdeInitializeW(
            ctypes.byref(_ddeml_inst), _dde_callback, _APPCMD_CLIENTONLY, 0
        )
        if ret != 0:
            logger.warning(f"DDEML DdeInitializeW 失敗 ret={ret}，AvgPrice 將使用 pywin32 回退值")
            return False
        hsz_svc   = _user32.DdeCreateStringHandleW(_ddeml_inst.value, "XQFAP", _CP_WINUNICODE)
        hsz_topic = _user32.DdeCreateStringHandleW(_ddeml_inst.value, "Quote", _CP_WINUNICODE)
        hconv = _user32.DdeConnect(_ddeml_inst.value, hsz_svc, hsz_topic, None)
        _user32.DdeFreeStringHandle(_ddeml_inst.value, hsz_svc)
        _user32.DdeFreeStringHandle(_ddeml_inst.value, hsz_topic)
        if not hconv:
            logger.warning("DDEML DdeConnect 失敗，AvgPrice 將使用 pywin32 回退值")
            return False
        _ddeml_hconv = hconv
        logger.info("DDEML 連線成功（advise + AvgPrice）")
        return True
    except Exception as e:
        logger.warning(f"DDEML 連線例外：{e}，AvgPrice 將使用 pywin32 回退值")
        return False


def _req_ddeml(item: str) -> str:
    """DDEML request；失敗回傳空字串（上層 fallback 到 pywin32）"""
    if not _ddeml_hconv:
        return ''
    try:
        hsz_item = _user32.DdeCreateStringHandleW(_ddeml_inst.value, item, _CP_WINUNICODE)
        dr = _DWORD(0)
        hdata = _user32.DdeClientTransaction(
            None, 0, _ddeml_hconv, hsz_item,
            _CF_TEXT, _XTYP_REQUEST, _DDE_TIMEOUT_MS, ctypes.byref(dr)
        )
        _user32.DdeFreeStringHandle(_ddeml_inst.value, hsz_item)
        if not hdata:
            return ''
        try:
            sz  = _user32.DdeGetData(hdata, None, 0, 0)
            buf = ctypes.create_string_buffer(sz) if sz > 0 else ctypes.create_string_buffer(1)
            _user32.DdeGetData(hdata, buf, sz, 0)
            val = buf.raw.rstrip(b'\x00').decode('cp950', errors='replace').strip()
            return '' if val == '-' else val
        finally:
            _user32.DdeFreeDataHandle(hdata)
    except Exception:
        return ''


def _get_avg_price(symbol: str) -> float:
    """先用 DDEML 讀 AvgPrice（正確小數），失敗才 fallback 到 pywin32"""
    item = f"{symbol}.TF-AvgPrice"
    val  = _req_ddeml(item)
    if not val:
        val = _req(item)   # pywin32 fallback（小數可能截斷，但優於 0）
    return _to_float(val)


# ── 合約資料讀取 ───────────────────────────────────────────────

def _get_fields(symbol: str) -> "dict | None":
    """
    Name 無效 → 合約不存在 → 回傳 None。
    InOutRatio = OutSize/TotalVolume×100（含開盤競價）。
    AvgPrice 優先 DDEML（正確小數），失敗 fallback pywin32。
    """
    name = _req(f"{symbol}.TF-Name")
    if not name:
        return None
    return {
        'total_volume': _to_float(_req(f"{symbol}.TF-TotalVolume")),
        'inout_ratio':  _to_float(_req(f"{symbol}.TF-InOutRatio")),
        'avg_price':    _get_avg_price(symbol),
    }


# ── 多執行緒 DDEML 輪詢池 ────────────────────────────────────────
# 每個 worker thread 各自持有一條 DDEML 連線（threading.local lazy init）
# discover 仍用 pywin32；poll 改走這裡

_thread_local = threading.local()


def _thread_ddeml_connect() -> bool:
    """在當前 thread 建立獨立 DDEML 連線，存入 threading.local"""
    inst = _DWORD(0)
    cb   = _PFNCALLBACK(_null_cb)
    ret  = _user32.DdeInitializeW(ctypes.byref(inst), cb, _APPCMD_CLIENTONLY, 0)
    if ret != 0:
        _thread_local.inst  = None
        _thread_local.hconv = None
        return False
    hsz_svc   = _user32.DdeCreateStringHandleW(inst.value, "XQFAP", _CP_WINUNICODE)
    hsz_topic = _user32.DdeCreateStringHandleW(inst.value, "Quote",  _CP_WINUNICODE)
    hconv     = _user32.DdeConnect(inst.value, hsz_svc, hsz_topic, None)
    _user32.DdeFreeStringHandle(inst.value, hsz_svc)
    _user32.DdeFreeStringHandle(inst.value, hsz_topic)
    if not hconv:
        _user32.DdeUninitialize(inst.value)
        _thread_local.inst  = None
        _thread_local.hconv = None
        return False
    _thread_local.inst  = inst
    _thread_local.hconv = hconv
    return True


def _req_thread(item: str) -> str:
    """用 thread-local DDEML 連線讀取 item；失敗回傳空字串。
    自動 lazy init；若連線中斷則標記重連（下次呼叫時重建）。
    注意：DDEML 的 InOutRatio 帶 '%' 後綴，此處統一 rstrip('%')。
    """
    if not getattr(_thread_local, 'hconv', None):
        if not _thread_ddeml_connect():
            return ''
    try:
        inst  = _thread_local.inst
        hconv = _thread_local.hconv
        hsz   = _user32.DdeCreateStringHandleW(inst.value, item, _CP_WINUNICODE)
        dr    = _DWORD(0)
        hdata = _user32.DdeClientTransaction(
            None, 0, hconv, hsz,
            _CF_TEXT, _XTYP_REQUEST, _DDE_TIMEOUT_MS, ctypes.byref(dr)
        )
        _user32.DdeFreeStringHandle(inst.value, hsz)
        if not hdata:
            # 連線已斷，先 DdeDisconnect + DdeUninitialize 再重連
            try:
                _user32.DdeDisconnect(hconv)
            except Exception:
                pass
            old_inst = getattr(_thread_local, 'inst', None)
            if old_inst:
                try:
                    _user32.DdeUninitialize(old_inst.value)
                except Exception:
                    pass
            _thread_local.hconv = None
            _thread_local.inst  = None
            return ''
        sz  = _user32.DdeGetData(hdata, None, 0, 0)
        buf = ctypes.create_string_buffer(sz) if sz > 0 else ctypes.create_string_buffer(1)
        _user32.DdeGetData(hdata, buf, sz, 0)
        _user32.DdeFreeDataHandle(hdata)
        val = buf.raw.rstrip(b'\x00').decode('cp950', errors='replace').strip()
        return '' if val in ('-', '') else val.rstrip('%')
    except Exception:
        # 例外路徑：同樣先 disconnect + uninitialize 再清除
        hconv = getattr(_thread_local, 'hconv', None)
        if hconv:
            try:
                _user32.DdeDisconnect(hconv)
            except Exception:
                pass
        old_inst = getattr(_thread_local, 'inst', None)
        if old_inst:
            try:
                _user32.DdeUninitialize(old_inst.value)
            except Exception:
                pass
        _thread_local.hconv = None
        _thread_local.inst  = None
        return ''




def _get_center_price() -> int:
    # 優先 DDEML（精確小數），fallback pywin32（FITX00）
    val   = _req_thread("FITXN*1.TF-Price") or _req("FITX00.TF-Price")
    price = _to_float(val)
    if price > 0:
        center = int(round(price / STRIKE_STEP) * STRIKE_STEP)
        logger.info(f"台指期現價 {price:.1f}，履約價搜尋中心 {center}")
        return center
    logger.warning("無法取得台指期現價，使用預設中心 32000")
    return 32000


# ── 合約探索（v2.7 架構：先 UP 再 DN）────────────────────────

def _discover_contracts(center: int, full_series: str) -> "tuple[list, dict]":
    """
    pywin32 DDE 探索：先從 center 往上，再從 center-50 往下。
    full_series 為完整系列碼，如 "TX4N03"、"TXYN03"。
    """
    logger.info(f"探索合約：{full_series} 系列，從 {center} 向兩側展開（連續{MISS_LIMIT}個miss即停）")
    found: dict[int, dict[str, bool]] = {}

    def _probe_direction(start: int, step: int):
        miss   = 0
        strike = start
        while miss < MISS_LIMIT:
            hit = False
            for side in ('C', 'P'):
                symbol = f"{full_series}{side}{strike}"
                name   = _req(f"{symbol}.TF-Name")
                if name:
                    found.setdefault(strike, {})[side] = True
                    hit = True
            miss = 0 if hit else miss + 1
            strike += step

    _probe_direction(center,              +STRIKE_STEP)
    _probe_direction(center - STRIKE_STEP, -STRIKE_STEP)

    contracts, meta = [], {}
    found_c = found_p = 0
    for strike in sorted(found.keys()):
        for side in ('C', 'P'):
            if not found[strike].get(side):
                continue
            symbol = f"{full_series}{side}{strike}"
            contracts.append({'symbol': symbol, 'strike': strike,
                               'side': side, 'prev_close': 0.0})
            meta[symbol] = {'strike': strike, 'side': side}
            if side == 'C': found_c += 1
            else:           found_p += 1

    if found:
        strikes = sorted(found.keys())
        logger.info(
            f"探索完成 {full_series}：Call {found_c} 個，Put {found_p} 個，"
            f"共 {len(contracts)} 個合約（{strikes[0]}～{strikes[-1]}）"
        )
    return contracts, meta


# ── HTTP 推送 ─────────────────────────────────────────────────

def _post_init(contracts: list, series: str, settlement_date: str = ""):
    sd = settlement_date or ""
    try:
        r = _http.post(
            f"{SERVER_URL}/api/init",
            json={'settlement_date': sd, 'contracts': contracts, 'series': series},
            timeout=10,
        )
        logger.info(f"POST /api/init [{series}] → HTTP {r.status_code}，{len(contracts)} 個合約")
    except Exception as e:
        logger.error(f"POST /api/init 失敗：{e}")


def _push_futures_price(price: float = 0.0):
    """推送 FITX*1 現價給 main.py，供 ATM 計算使用。
    price 直接傳入時優先使用；
    否則用 DDEML 讀 FITXN*1.TF-Price（精確小數，無 pywin32 截斷問題）。
    """
    try:
        if price > 0:
            val = price
        else:
            val = _to_float(_req_thread("FITXN*1.TF-Price"))
        if val > 0:
            _http.post(f"{SERVER_URL}/api/set-futures-price", json={"price": val}, timeout=2)
            logger.info(f"FITX 現價推送：{val}")
        else:
            logger.warning("FITXN*1.TF-Price 回傳 0 或空值，略過推送")
    except Exception as e:
        logger.warning(f"_push_futures_price 失敗：{e}")


def _post_feed(batch: list, series: str):
    try:
        r = _http.post(f"{SERVER_URL}/api/feed?series={series}", json=batch, timeout=5)
        if r.status_code != 200:
            logger.warning(f"POST /api/feed [{series}] HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"POST /api/feed 失敗：{e}")


# ── 初始快照 ──────────────────────────────────────────────────

def _push_snapshot(meta: dict, series: str):
    """初始快照：TotalVolume + InOutRatio 用 pywin32；AvgPrice 用 _req_thread（DDEML，正確小數）。"""
    logger.info(f"開始 push_snapshot [{series}]：{len(meta)} 筆")
    snapshot = []
    for symbol in meta:
        snapshot.append({
            'symbol':       symbol,
            'trade_volume': int(_to_float(_req(f"{symbol}.TF-TotalVolume")) or 0),
            'inout_ratio':  _to_float(_req(f"{symbol}.TF-InOutRatio")),
            'avg_price':    _to_float(_req_thread(f"{symbol}.TF-AvgPrice")),
            'bid_price':    _to_float(_req_thread(f"{symbol}.TF-Bid")),
            'ask_price':    _to_float(_req_thread(f"{symbol}.TF-Ask")),
            'last_price':   _to_float(_req_thread(f"{symbol}.TF-Price")),
        })
    if snapshot:
        _post_feed(snapshot, series=series)
        logger.info(f"初始快照推送完成 [{series}]：{len(snapshot)} 筆")


# ── 追蹤狀態 ─────────────────────────────────────────────────
# _all_metas:  {series → meta_dict}，包含所有被追蹤系列的 full + day
# _all_prevs:  {series → prev_dict}，用於偵測成交量變動
# _all_valid_series: scan 到的全部有效系列（供 _post_contracts 更新 live 旗標）

_all_metas:        dict = {}
_all_prevs:        dict = {}
_all_valid_series: list = []

_reinit_flag = threading.Event()


def _load_one_series(center: int, full_series: str, sd_str: str):
    """探索並初始化單一系列，加入 _all_metas，並通知 advise 迴圈訂閱"""
    _series_sd[full_series] = sd_str
    contracts_full, meta_full = _discover_contracts(center, full_series)
    if not contracts_full:
        logger.warning(f"載入 {full_series} 失敗，跳過")
        return
    day_series              = full_series.replace('N', '')
    contracts_day, meta_day = _build_day_meta(meta_full, full_series)
    _post_init(contracts_full, full_series, sd_str)
    _post_init(contracts_day,  day_series,  sd_str)
    _all_metas[full_series] = meta_full
    _all_metas[day_series]  = meta_day
    _all_prevs[full_series] = {}
    _all_prevs[day_series]  = {}
    _push_snapshot(meta_full, full_series)
    _push_snapshot(meta_day,  day_series)
    _push_futures_price(float(center))
    logger.info(f"載入完成：{full_series} / {day_series}")
    # 通知 advise 迴圈訂閱新系列
    with _pending_subscribe_lock:
        _pending_subscribe.update(meta_full.keys())
    if _advise_loop_tid:
        _user32.PostThreadMessageW(_advise_loop_tid, _WM_APP_SUBSCRIBE, 0, 0)
    _post_contracts(_all_valid_series)


# ── DDE Advise 架構 ───────────────────────────────────────────

def _rebuild_sym_to_series():
    """重建 symbol → series 反查表（只含 full series，即帶 N 的）"""
    global _sym_to_series
    _sym_to_series = {
        sym: series
        for series, meta in _all_metas.items()
        if 'N' in series
        for sym in meta
    }


def _advise_subscribe(symbols: list):
    """對 symbols 發出 XTYP_ADVSTART 訂閱（TIMEOUT_ASYNC，全部 TF-TotalVolume）"""
    if not _ddeml_hconv:
        logger.error("_advise_subscribe：DDEML 未連線")
        return
    ok = fail = 0
    for sym in symbols:
        item_str = f"{sym}.TF-TotalVolume"
        hsz = _user32.DdeCreateStringHandleW(_ddeml_inst.value, item_str, _CP_WINUNICODE)
        try:
            hdata = _user32.DdeClientTransaction(
                None, 0, _ddeml_hconv, hsz,
                _CF_TEXT, _XTYP_ADVSTART, _TIMEOUT_ASYNC, None,
            )
        finally:
            _user32.DdeFreeStringHandle(_ddeml_inst.value, hsz)
        if hdata:
            _user32.DdeFreeDataHandle(hdata)
            ok += 1
        else:
            fail += 1
    logger.info(f"ADVSTART：{ok} 成功，{fail} 失敗（共 {len(symbols)} 個合約）")


def _advise_unsubscribe(symbols: list):
    """對 symbols 發出 XTYP_ADVSTOP，取消 TF-TotalVolume advise 訂閱"""
    if not _ddeml_hconv:
        return
    for sym in symbols:
        item_str = f"{sym}.TF-TotalVolume"
        hsz = _user32.DdeCreateStringHandleW(_ddeml_inst.value, item_str, _CP_WINUNICODE)
        try:
            dr = ctypes.c_ulong(0)
            _user32.DdeClientTransaction(
                None, 0, _ddeml_hconv, hsz,
                _CF_TEXT, _XTYP_ADVSTOP, 5000, ctypes.byref(dr),
            )
        finally:
            _user32.DdeFreeStringHandle(_ddeml_inst.value, hsz)
    logger.info(f"ADVSTOP：{len(symbols)} 個合約取消訂閱")


_BATCH_WINDOW = 0.5   # 秒：每 0.5s 批次 REQUEST ratio/avg 並推送


def _fetch_one_changed(series: str, symbol: str, new_vol: int):
    """advise_worker 的單一 symbol DDE fetch，跑在 _advise_req_executor 的 thread 上。"""
    day_series = series.replace('N', '')
    has_day    = day_series in _all_metas and day_series != series

    ratio_str = _req_thread(f"{symbol}.TF-InOutRatio")
    avg_str   = _req_thread(f"{symbol}.TF-AvgPrice")
    bid_str   = _req_thread(f"{symbol}.TF-Bid")
    ask_str   = _req_thread(f"{symbol}.TF-Ask")
    last_str  = _req_thread(f"{symbol}.TF-Price")
    new_ratio = _to_float(ratio_str)
    new_avg   = _to_float(avg_str)
    prev = _all_prevs.get(series)
    if prev is not None:
        prev[symbol] = (new_ratio, new_vol)
    full_item = {'series': series, 'symbol': symbol,
                 'trade_volume': new_vol, 'inout_ratio': new_ratio, 'avg_price': new_avg,
                 'bid_price': _to_float(bid_str), 'ask_price': _to_float(ask_str),
                 'last_price': _to_float(last_str)}

    day_item = None
    if has_day:
        day_sym     = symbol.replace(series, day_series, 1)
        d_vol_str   = _req_thread(f"{day_sym}.TF-TotalVolume")
        d_ratio_str = _req_thread(f"{day_sym}.TF-InOutRatio")
        d_avg_str   = _req_thread(f"{day_sym}.TF-AvgPrice")
        d_last_str  = _req_thread(f"{day_sym}.TF-Price")
        d_vol   = int(float(d_vol_str)) if d_vol_str else 0
        d_ratio = _to_float(d_ratio_str)
        d_avg   = _to_float(d_avg_str)
        d_last  = _to_float(d_last_str)
        day_prev = _all_prevs.get(day_series)
        if day_prev is not None:
            day_prev[day_sym] = (d_ratio, d_vol)
        day_item = {'series': day_series, 'symbol': day_sym,
                    'trade_volume': d_vol, 'inout_ratio': d_ratio, 'avg_price': d_avg,
                    'last_price': d_last}

    return full_item, day_item


def _advise_worker():
    """
    批次 worker：收集 _change_queue 的 TotalVolume 變動，
    每 _BATCH_WINDOW 秒批次 REQUEST InOutRatio + AvgPrice（平行 4 threads），推送 FastAPI。
    """
    logger.info("advise worker 啟動")
    with ThreadPoolExecutor(max_workers=_ADVISE_REQ_THREADS,
                            thread_name_prefix='advise_req') as executor:
        while True:
            deadline = time.time() + _BATCH_WINDOW
            changed: dict = {}   # (series, symbol) → new_vol

            while time.time() < deadline:
                try:
                    timeout = max(0.01, deadline - time.time())
                    series, symbol, new_vol = _change_queue.get(timeout=timeout)
                except queue.Empty:
                    break
                prev    = _all_prevs.get(series, {})
                old_vol = prev.get(symbol, (0, -1))[1]
                if new_vol != old_vol:
                    changed[(series, symbol)] = new_vol

            if not changed:
                continue

            # 平行 DDE requests（4 threads，各持 thread-local DDEML 連線）
            futures = {
                executor.submit(_fetch_one_changed, series, symbol, new_vol): (series, symbol)
                for (series, symbol), new_vol in changed.items()
            }

            by_series:     dict = {}
            by_series_day: dict = {}
            for fut in as_completed(futures):
                try:
                    full_item, day_item = fut.result()
                    s = full_item.pop('series')
                    by_series.setdefault(s, []).append(full_item)
                    if day_item:
                        ds = day_item.pop('series')
                        by_series_day.setdefault(ds, []).append(day_item)
                except Exception as e:
                    logger.warning(f"advise_worker fetch 失敗: {e}")

            for series, batch in by_series.items():
                _post_feed(batch, series)
            for day_series, batch in by_series_day.items():
                _post_feed(batch, day_series)
            _push_futures_price()


def _quote_poll_worker():
    """
    定時輪詢所有欄位，每 _QUOTE_POLL_INTERVAL 秒對 active series 全部合約並行 REQUEST
    六個欄位（Bid/Ask/Price/TotalVolume/InOutRatio/AvgPrice），與前次比對只推有變化的合約。
    取代 ADVISE 觸發的成交量更新，讓全部欄位都能 0.5s 刷新。
    """
    logger.info("quote poll worker 啟動")

    def _fetch_quote(symbol: str):
        bid  = _to_float(_req_thread(f"{symbol}.TF-Bid"))
        ask  = _to_float(_req_thread(f"{symbol}.TF-Ask"))
        last = _to_float(_req_thread(f"{symbol}.TF-Price"))
        vol_str = _req_thread(f"{symbol}.TF-TotalVolume")
        vol  = int(float(vol_str)) if vol_str and vol_str != '-' else 0
        ratio = _to_float(_req_thread(f"{symbol}.TF-InOutRatio"))
        avg  = _to_float(_req_thread(f"{symbol}.TF-AvgPrice"))
        return symbol, bid, ask, last, vol, ratio, avg

    with ThreadPoolExecutor(max_workers=_QUOTE_POLL_THREADS,
                            thread_name_prefix='quote_req') as executor:
        while True:
            series = _active_advise_series
            if not series:
                time.sleep(0.05)
                continue
            meta = _all_metas.get(series, {})
            symbols = list(meta.keys())
            if not symbols:
                time.sleep(0.05)
                continue

            try:
                t0 = time.time()
                futures = {executor.submit(_fetch_quote, sym): sym for sym in symbols}
                changed = []
                for fut in as_completed(futures):
                    try:
                        symbol, bid, ask, last, vol, ratio, avg = fut.result()
                    except Exception:
                        continue
                    prev = _quote_prevs.get(symbol)
                    cur = (bid, ask, last, vol, ratio, avg)
                    if prev and prev == cur:
                        continue
                    _quote_prevs[symbol] = cur
                    item = {'symbol': symbol, 'bid_price': bid, 'ask_price': ask, 'last_price': last}
                    if vol > 0:
                        item['trade_volume'] = vol
                        item['inout_ratio']  = ratio
                        item['avg_price']    = avg
                    else:
                        item['trade_volume'] = 0
                    changed.append(item)

                elapsed = time.time() - t0
                logger.info(f"[quote_poll] {len(symbols)} 合約，{len(changed)} 筆變化，耗時 {elapsed*1000:.0f}ms")
                if changed:
                    _post_feed(changed, series)
                    day_series = series.replace('N', '')
                    _now = datetime.datetime.now().time()
                    if (day_series != series and day_series in _all_metas
                            and datetime.time(8, 45) <= _now <= datetime.time(13, 45)):
                        day_meta    = _all_metas[day_series]
                        day_changed = []
                        for item in changed:
                            day_sym = item['symbol'].replace(series, day_series, 1)
                            if day_sym in day_meta:
                                day_changed.append({
                                    'symbol':       day_sym,
                                    'trade_volume': 0,
                                    'bid_price':    item['bid_price'],
                                    'ask_price':    item['ask_price'],
                                    'last_price':   item['last_price'],
                                })
                        if day_changed:
                            logger.info(f"[mirror] {day_series} {len(day_changed)} 筆 bid/ask/last")
                            _post_feed(day_changed, day_series)
                    else:
                        logger.debug(f"[mirror] skip: day_series={day_series!r} in_metas={day_series in _all_metas}")
                _push_futures_price()
            except Exception:
                logger.error(f"[quote_poll] 未預期例外，1 秒後重試：\n{traceback.format_exc()}")
                time.sleep(1)


def _bulk_request_series(full_series: str):
    """
    Worker thread：對 full_series 及其對應 day_series 的所有合約做一次全量 REQUEST，
    更新 _all_prevs 並 POST 到 FastAPI。切換 active series 後呼叫，補上錯過的資料。
    _BULK_REQ_THREADS 條並行 DDEML 連線（各 thread-local），縮短等待時間。
    _bulk_req_sem 確保同時只有 1 個 bulk_req 執行，避免多系列並行競爭 DDE 資源。

    兩階段設計：
      Phase 1：只 REQUEST full_series（全日盤，6 fields）→ series-ready TX1N04
      Phase 2：只 REQUEST day_series（日盤，3 fields）→ series-ready TX104
    TX1N04 能比原本更早 ready，UI 可先顯示全日盤資料。
    """
    _bulk_req_sem.acquire()
    t0         = time.time()
    day_series = full_series.replace('N', '')
    full_meta  = _all_metas.get(full_series, {})
    symbols    = list(full_meta.keys())
    has_day    = day_series in _all_metas

    n_threads  = min(_BULK_REQ_THREADS, len(symbols)) if symbols else 1
    # interleaved slicing：讓各 thread 均勻分散到不同履約價
    chunks     = [symbols[i::n_threads] for i in range(n_threads)]

    def _cleanup_thread():
        """釋放 thread-local DDEML 連線（Windows 系統資源，GC 無法自動回收）"""
        hconv = getattr(_thread_local, 'hconv', None)
        inst  = getattr(_thread_local, 'inst',  None)
        if hconv:
            try:
                _user32.DdeDisconnect(hconv)
            except Exception:
                pass
        if inst:
            try:
                _user32.DdeUninitialize(inst.value)
            except Exception:
                pass
        _thread_local.hconv = None
        _thread_local.inst  = None

    try:
        # ── Phase 1: full_series（全日盤，6 fields）────────────────────────
        full_results: list = []
        merge_lock_full = threading.Lock()

        def _worker_full(chunk: list):
            local_full: list = []
            try:
                for full_sym in chunk:
                    vol_str   = _req_thread(f"{full_sym}.TF-TotalVolume")
                    ratio_str = _req_thread(f"{full_sym}.TF-InOutRatio")
                    avg_str   = _req_thread(f"{full_sym}.TF-AvgPrice")
                    bid_str   = _req_thread(f"{full_sym}.TF-Bid")
                    ask_str   = _req_thread(f"{full_sym}.TF-Ask")
                    last_str  = _req_thread(f"{full_sym}.TF-Price")
                    new_vol   = int(float(vol_str)) if vol_str else 0
                    new_ratio = _to_float(ratio_str)
                    new_avg   = _to_float(avg_str)
                    # bg_poll 永遠送出，即使 vol=0，讓 main.py 更新 last_updated（盤後心跳用）
                    local_full.append({'symbol': full_sym, 'trade_volume': new_vol,
                                       'inout_ratio': new_ratio, 'avg_price': new_avg,
                                       'bid_price': _to_float(bid_str),
                                       'ask_price': _to_float(ask_str),
                                       'last_price': _to_float(last_str),
                                       '_ratio': new_ratio, '_vol': new_vol})
                with merge_lock_full:
                    full_results.extend(local_full)
            finally:
                _cleanup_thread()

        threads_p1 = [threading.Thread(target=_worker_full, args=(chunk,), daemon=True)
                      for chunk in chunks]
        for t in threads_p1:
            t.start()
        for t in threads_p1:
            t.join()

        # 更新 _all_prevs + POST + series-ready for full_series
        fp = _all_prevs.get(full_series)
        if fp is not None:
            for item in full_results:
                fp[item['symbol']] = (item['_ratio'], item['trade_volume'])
        full_batch = [{k: v for k, v in r.items() if not k.startswith('_')}
                      for r in full_results]
        elapsed1 = time.time() - t0
        if full_batch:
            _post_feed(full_batch, full_series)
            logger.info(f"[bulk_req] Phase1 {full_series} {len(full_batch)} 筆"
                        f"（{n_threads} threads，{elapsed1:.1f}s）")
        try:
            _http.post(f"{SERVER_URL}/api/series-ready?series={full_series}", timeout=3)
            logger.info(f"[bulk_req] series-ready: {full_series}")
        except Exception as _e:
            logger.warning(f"[bulk_req] series-ready 失敗 {full_series}: {_e}")
        # ── Phase 2: day_series（日盤，3 fields）──────────────────────────
        if has_day:
            day_results: list = []
            merge_lock_day = threading.Lock()

            def _worker_day(chunk: list):
                local_day: list = []
                try:
                    for full_sym in chunk:
                        day_sym     = full_sym.replace(full_series, day_series, 1)
                        d_vol_str   = _req_thread(f"{day_sym}.TF-TotalVolume")
                        d_ratio_str = _req_thread(f"{day_sym}.TF-InOutRatio")
                        d_avg_str   = _req_thread(f"{day_sym}.TF-AvgPrice")
                        d_bid_str   = _req_thread(f"{day_sym}.TF-Bid")
                        d_ask_str   = _req_thread(f"{day_sym}.TF-Ask")
                        d_last_str  = _req_thread(f"{day_sym}.TF-Price")
                        d_vol   = int(float(d_vol_str)) if d_vol_str else 0
                        d_ratio = _to_float(d_ratio_str)
                        d_avg   = _to_float(d_avg_str)
                        local_day.append({'symbol': day_sym, 'trade_volume': d_vol,
                                          'inout_ratio': d_ratio, 'avg_price': d_avg,
                                          'bid_price': _to_float(d_bid_str),
                                          'ask_price': _to_float(d_ask_str),
                                          'last_price': _to_float(d_last_str),
                                          '_day_sym': day_sym, '_ratio': d_ratio, '_vol': d_vol})
                    with merge_lock_day:
                        day_results.extend(local_day)
                finally:
                    _cleanup_thread()

            threads_p2 = [threading.Thread(target=_worker_day, args=(chunk,), daemon=True)
                          for chunk in chunks]
            for t in threads_p2:
                t.start()
            for t in threads_p2:
                t.join()

            # 更新 _all_prevs + POST + series-ready for day_series
            dp = _all_prevs.get(day_series)
            if dp is not None:
                for item in day_results:
                    dp[item['symbol']] = (item['_ratio'], item['trade_volume'])
            day_batch = [{k: v for k, v in r.items() if not k.startswith('_')}
                         for r in day_results]
            elapsed2 = time.time() - t0
            if day_batch:
                _post_feed(day_batch, day_series)
                logger.info(f"[bulk_req] Phase2 {day_series} {len(day_batch)} 筆"
                            f"（{elapsed2:.1f}s）")
            try:
                _http.post(f"{SERVER_URL}/api/series-ready?series={day_series}", timeout=3)
                logger.info(f"[bulk_req] series-ready: {day_series}")
            except Exception as _e:
                logger.warning(f"[bulk_req] series-ready 失敗 {day_series}: {_e}")
    finally:
        _bulk_req_sem.release()


def _switch_active_series(new_full_series: str):
    """
    主執行緒（message loop）呼叫：切換 advise 訂閱至 new_full_series。
    1. ADVSTOP 舊系列
    2. ADVSTART 新系列
    3. 背景 thread 做一次 bulk REQUEST 補資料
    """
    global _active_advise_series
    old = _active_advise_series

    if old and old != new_full_series:
        old_syms = list(_all_metas.get(old, {}).keys())
        logger.info(f"[switch] ADVSTOP {old}（{len(old_syms)} 個）")
        _advise_unsubscribe(old_syms)

    new_syms = list(_all_metas.get(new_full_series, {}).keys())
    if new_syms:
        logger.info(f"[switch] ADVSTART {new_full_series}（{len(new_syms)} 個）")
        _advise_subscribe(new_syms)
        _active_advise_series = new_full_series
        _rebuild_sym_to_series()
        # 背景補資料（不阻塞 message loop）
        threading.Thread(target=_bulk_request_series,
                         args=(new_full_series,), daemon=True).start()
    else:
        logger.warning(f"[switch] {new_full_series} 尚未載入，無法切換")


def _trigger_switch(new_full: str):
    """從任何 thread 觸發 series 切換：設 pending + PostThreadMessageW。"""
    global _pending_switch_series
    if (new_full and new_full in _all_metas
            and new_full != _active_advise_series):
        with _pending_switch_lock:
            _pending_switch_series = new_full
        if _advise_loop_tid:
            _user32.PostThreadMessageW(
                _advise_loop_tid, _WM_APP_SWITCH, 0, 0)


def _switch_listener():
    """
    TCP socket listener（port 8001）：接收 main.py 的即時切換通知。
    main.py 呼叫 /api/set-series 時直接 connect 並送 series 名稱，
    省去 _series_watcher 的 2s 輪詢延遲。
    """
    import socket as _socket
    srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    try:
        srv.bind(('127.0.0.1', _SWITCH_LISTENER_PORT))
    except OSError as e:
        logger.warning(f"[switch_listener] bind 失敗：{e}，fallback 到輪詢")
        srv.close()
        return
    srv.listen(8)
    logger.info(f"[switch_listener] 監聽 127.0.0.1:{_SWITCH_LISTENER_PORT}")
    while True:
        try:
            conn, _ = srv.accept()
            data = conn.recv(64).decode('utf-8', errors='replace').strip()
            conn.close()
            if data:
                _trigger_switch(data)
        except Exception:
            pass


def _series_watcher():
    """
    背景 fallback：每 10s 輪詢 /api/active-series，補抓 socket 通知遺漏的切換。
    主要切換路徑已改為 _switch_listener（即時觸發）。
    """
    global _pending_switch_series
    last_seen = ''
    while True:
        try:
            resp = _http.get(f"{SERVER_URL}/api/active-series", timeout=3)
            if resp.ok:
                new_full = resp.json().get('full', '')
                if new_full:
                    if (new_full != last_seen
                            and new_full != _active_advise_series):
                        _trigger_switch(new_full)
                    last_seen = new_full
        except Exception:
            pass
        time.sleep(10)


def _bg_poll_one_series(full_series: str, offset: float):
    """
    背景 thread：每 _BG_POLL_INTERVAL 秒輪詢一次。
    - active series：只發心跳（ADVISE + quote_poll 已即時更新，不做全量避免 race condition）
    - 背景 series：做全量 REQUEST 補資料（無 ADVISE，不會 race）
    """
    time.sleep(offset)
    while True:
        if full_series == _active_advise_series:
            day_series = full_series.replace('N', '')
            logger.info(f"[bg_poll] 心跳 {full_series} / {day_series}")
            try:
                _http.post(f"{SERVER_URL}/api/heartbeat?series={full_series}", timeout=2)
            except Exception:
                pass
            try:
                _http.post(f"{SERVER_URL}/api/heartbeat?series={day_series}", timeout=2)
            except Exception:
                pass
        else:
            logger.info(f"[bg_poll] 輪詢 {full_series}")
            _bulk_request_series(full_series)
        time.sleep(_BG_POLL_INTERVAL)


def _reconnect_and_resubscribe():
    """
    DDEML 重新連線並重訂 advise（watchdog 或 DISCONNECT 觸發）。
    必須在 advise loop 的主執行緒上呼叫。
    """
    global _ddeml_hconv, _last_resubscribe_time
    now = time.time()
    if now - _last_resubscribe_time < _RESUBSCRIBE_COOLDOWN:
        return   # 冷卻中，不重複重連
    _last_resubscribe_time = now

    logger.warning("[watchdog] 重新連線 DDEML 並重訂 advise...")
    if _ddeml_hconv:
        try:
            _user32.DdeDisconnect(_ddeml_hconv)
        except Exception:
            pass
    hsz_svc   = _user32.DdeCreateStringHandleW(_ddeml_inst.value, "XQFAP", _CP_WINUNICODE)
    hsz_topic = _user32.DdeCreateStringHandleW(_ddeml_inst.value, "Quote",  _CP_WINUNICODE)
    hconv = _user32.DdeConnect(_ddeml_inst.value, hsz_svc, hsz_topic, None)
    _user32.DdeFreeStringHandle(_ddeml_inst.value, hsz_svc)
    _user32.DdeFreeStringHandle(_ddeml_inst.value, hsz_topic)
    if not hconv:
        logger.error("[watchdog] 重新連線失敗，下次 timer 再試")
        _ddeml_hconv = None
        return
    _ddeml_hconv = hconv
    # 重訂目前 active series（若無則用第一個）
    target = _active_advise_series or next((s for s in _all_metas if 'N' in s), None)
    if target:
        syms = list(_all_metas[target].keys())
        _advise_subscribe(syms)
        logger.info(f"[watchdog] 重新訂閱完成（{target}，{len(syms)} 個）")
        # 重連後補一次 bulk_req，回補斷線期間遺失的 TotalVolume 變動
        threading.Thread(target=_bulk_request_series, args=(target,), daemon=True).start()
        logger.info(f"[watchdog] 啟動補拉 bulk_req（{target}）")


def _advise_loop():
    """
    主 advise 迴圈：GetMessageW blocking loop，由 DDEML dispatch advise callback。
    WM_APP_REINIT（0x8001）：重新初始化並重訂 advise。
    WM_APP_SUBSCRIBE（0x8002）：訂閱 _pending_subscribe 中的新合約。
    WM_TIMER（0x0113）：watchdog，偵測 callback 停止並自動重連。
    """
    global _advise_loop_tid
    _kernel32 = ctypes.WinDLL('kernel32')
    _kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
    _advise_loop_tid = _kernel32.GetCurrentThreadId()
    logger.info(f"advise 訊息迴圈啟動（Win32 TID={_advise_loop_tid}）")

    # 訂閱第一個（最近到期）full series，設為 active
    global _active_advise_series
    first_series = next((s for s in _all_metas if 'N' in s), None)
    if first_series:
        syms = list(_all_metas[first_series].keys())
        _advise_subscribe(syms)
        _active_advise_series = first_series
    else:
        logger.error("advise_loop: 找不到任何 full series")

    # 啟動 watchdog timer（每 30 秒觸發 WM_TIMER）
    _user32.SetTimer(None, _WATCHDOG_TIMER_ID, _WATCHDOG_INTERVAL, None)

    msg = ctypes.wintypes.MSG()
    hb_tick = 0
    while True:
        ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret == 0:   # WM_QUIT
            logger.info("advise 迴圈收到 WM_QUIT，退出")
            break
        if ret < 0:    # 錯誤
            logger.error(f"GetMessageW 錯誤 ret={ret}")
            break

        if msg.message == _WM_TIMER:
            elapsed = time.time() - _last_callback_time
            if _last_callback_time > 0 and elapsed > _WATCHDOG_THRESHOLD:
                _now_t = datetime.datetime.now().time()
                # 真正的盤後空窗（DDEML 本就不推 callback），只涵蓋兩個空窗：
                #   14:30~15:00  日盤收盤到夜盤開盤
                #   05:00~08:45  夜盤收盤到日盤開盤
                # 夜盤（15:00~05:00）與日盤（08:45~13:45）期間必須觸發重連。
                _after_market = (
                    (datetime.time(14, 30) <= _now_t < datetime.time(15, 0)) or
                    (datetime.time(5, 0)   <= _now_t < datetime.time(8, 45))
                )
                if _after_market:
                    pass  # 盤後靜默，不重連不重 init
                else:
                    logger.warning(f"[watchdog] {elapsed:.0f}s 無 ADVDATA，嘗試重新連線")
                    _reconnect_and_resubscribe()
            continue

        if msg.message == _WM_APP_REINIT:
            _reinit_flag.clear()
            logger.info("[排程] advise 迴圈處理 REINIT：重新探索並重訂 advise")
            _reinit()
            _rebuild_sym_to_series()
            for s, m in list(_all_metas.items()):
                _push_snapshot(m, s)
            # 只重訂第一個 series
            first = next((s for s in _all_metas if 'N' in s), None)
            if first:
                _advise_subscribe(list(_all_metas[first].keys()))
            continue

        if msg.message == _WM_APP_SUBSCRIBE:
            with _pending_subscribe_lock:
                syms = list(_pending_subscribe)
                _pending_subscribe.clear()
            if syms:
                _advise_subscribe(syms)
                _rebuild_sym_to_series()
            continue

        if msg.message == _WM_APP_SWITCH:
            with _pending_switch_lock:
                target = _pending_switch_series
            if target:
                _switch_active_series(target)
            continue

        hb_tick += 1
        if hb_tick % 600 == 0:
            _now_hb = time.time()
            elapsed_since_cb = _now_hb - _last_callback_time
            total = sum(len(v) for v in _all_prevs.values())
            # per-symbol 新鮮度：active series 中有多少 symbol 在最近 5 分鐘收過 callback
            active_syms = list(_all_metas.get(_active_advise_series, {}).keys())
            fresh_cutoff = _now_hb - 300
            fresh_count  = sum(1 for s in active_syms
                               if _last_callback_by_symbol.get(s, 0) > fresh_cutoff)
            logger.info(f"heartbeat: {[f'{s}={len(m)}' for s, m in _all_metas.items()]} "
                        f"prev={total} last_cb={elapsed_since_cb:.0f}s ago "
                        f"active={_active_advise_series} "
                        f"fresh={fresh_count}/{len(active_syms)}")

        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))


# ── 自動重新初始化排程 ────────────────────────────────────────

_REINIT_TIMES    = {(8, 43), (14, 58)}
_last_reinit_key = ""


def _build_day_meta(meta_full: dict, full_series: str) -> "tuple[list, dict]":
    """從 full meta 推導 day meta，例如 TX4N03 → TX403"""
    day_series = full_series.replace('N', '')
    contracts, meta = [], {}
    for old_sym, info in meta_full.items():
        suffix = old_sym[len(full_series):]   # e.g. "C32600"
        sym    = f"{day_series}{suffix}"
        contracts.append({'symbol': sym, 'strike': info['strike'],
                           'side': info['side'], 'prev_close': 0.0})
        meta[sym] = {'strike': info['strike'], 'side': info['side']}
    return contracts, meta


# 記錄正在追蹤的 full 系列清單（供 _reinit 使用）
_tracked_full_series: list = []
# 記錄各系列的結算日字串
_series_sd: dict = {}


def _reinit():
    """重新探索並初始化所有目前正在追蹤的系列（盤前排程觸發）"""
    global _all_metas, _all_prevs
    center     = _get_center_price()
    # 從 _all_metas 取出所有 full series（含 N 的），原 slow/fast 分類保持不變
    full_series_list = [s for s in _all_metas if 'N' in s]
    new_metas  = {}
    new_prevs  = {}
    for full_series in full_series_list:
        contracts_full, meta_full = _discover_contracts(center, full_series)
        if not contracts_full:
            logger.error(f"重新初始化失敗：{full_series} 找不到合約")
            continue
        day_series    = full_series.replace('N', '')
        contracts_day, meta_day = _build_day_meta(meta_full, full_series)
        sd = _series_sd.get(full_series, "")
        _post_init(contracts_full, full_series, sd)
        _post_init(contracts_day,  day_series,  sd)
        new_metas[full_series] = meta_full
        new_metas[day_series]  = meta_day
        new_prevs[full_series] = {}
        new_prevs[day_series]  = {}
    _all_metas = new_metas
    _all_prevs = new_prevs
    # 清除 _series_sd 中已不在追蹤清單的廢棄系列（與 _all_metas 保持同步）
    for stale in [k for k in list(_series_sd.keys()) if k not in new_metas]:
        del _series_sd[stale]
    logger.info(f"重新初始化完成：{list(_all_metas.keys())}")
    # 通知 main.py 清除已不在追蹤清單的廢棄系列（避免 stores 無限累積）
    try:
        _http.post(f"{SERVER_URL}/api/purge-series",
                   json={'keep': list(new_metas.keys())}, timeout=5)
    except Exception as e:
        logger.warning(f"purge-series 失敗：{e}")
    # 切換 active series 為新 default：after_cutoff 跳過今天結算的，取第一個未結算
    new_full_list = [s for s in new_metas if 'N' in s]
    if new_full_list:
        _today_str    = datetime.date.today().isoformat()
        _after_cutoff = datetime.datetime.now().time() >= datetime.time(14, 30)
        if _after_cutoff:
            _unsettled = [s for s in new_full_list if _series_sd.get(s, "9999") > _today_str]
            new_default_full = _unsettled[0] if _unsettled else new_full_list[0]
        else:
            new_default_full = new_full_list[0]
        new_default_day  = new_default_full.replace('N', '')
        try:
            _http.post(f"{SERVER_URL}/api/set-series",
                       json={'series_full': new_default_full, 'series_day': new_default_day},
                       timeout=5)
            logger.info(f"[reinit] 已切換 active → {new_default_full} / {new_default_day}")
        except Exception as e:
            logger.warning(f"[reinit] set-series 失敗：{e}")


def _auto_reinit_scheduler():
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
        logger.info(f"[排程] {now.strftime('%H:%M')} 盤前重新初始化...")
        if _advise_loop_tid:
            _user32.PostThreadMessageW(_advise_loop_tid, _WM_APP_REINIT, 0, 0)
        else:
            _reinit_flag.set()   # fallback（advise 未啟動時）


# ── 合約下拉清單推送 ──────────────────────────────────────────

def _post_contracts(found: list):
    """將有效系列清單（依到期日排序）推送到 main.py /api/contracts"""
    from core import taifex_calendar as tc
    now = datetime.datetime.now()
    _WD = ['一', '二', '三', '四', '五', '六', '日']
    contracts = []
    for series in found:
        n_idx  = series.index('N')
        prefix = series[:n_idx]
        month  = int(series[n_idx + 1:])
        year   = now.year if month >= now.month else now.year + 1
        sd     = tc.settlement_date(prefix, year, month)
        label  = tc.tf_name_label(prefix, month)
        sd_str     = str(sd) if sd else ''
        sd_display = f"{sd_str}({_WD[sd.weekday()]})" if sd else '--'
        contracts.append({
            'series':             series,
            'label':              label,
            'settlement_date':    sd_str,
            'settlement_display': sd_display,
        })
    contracts.sort(key=lambda c: c['settlement_date'] or '9999-99-99')
    try:
        _http.post(f"{SERVER_URL}/api/contracts",
                   json={'contracts': contracts}, timeout=5)
        logger.info(f"已推送合約下拉清單（{len(contracts)} 個系列）")
    except Exception as e:
        logger.warning(f"推送合約清單失敗：{e}")


# ── --discover 模式 ───────────────────────────────────────────

# TAIFEX 前綴清單（按到期順序）
# 週三：TX1=W1, TX2=W2, TXO=W3(月選), TX4=W4, TX5=W5(罕見)
# 週五：TXU=W1, TXV=W2, TXX=W3, TXY=W4, TXZ=W5(罕見)
_ALL_PREFIXES   = ['TX1','TX2','TXO','TX4','TX5','TXU','TXV','TXX','TXY','TXZ']
_WEEKLY_PREFIXES = [p for p in _ALL_PREFIXES if p != 'TXO']  # 週選（2個月後不存在）


def _scan_valid_series(center: int) -> list[str]:
    """
    掃描 XQFAP 中有效系列：當月 + 下個月，10 個前綴全掃，共 20 組測試。
    回傳有效系列碼清單，如 ['TX4N03','TXYN03','TX1N04',...]
    """
    now   = datetime.datetime.now()
    found = []

    # 測試點：center, +50, +100, +150（其中一個必中）
    test_strikes = [center, center + 50, center + 100, center + 150]

    for month_offset in range(2):   # 只掃當月 + 下個月
        m = now.month + month_offset
        month = f"{((m - 1) % 12) + 1:02d}"
        for prefix in _ALL_PREFIXES:
            series = f"{prefix}N{month}"
            for strike in test_strikes:
                sym  = f"{series}C{strike}"
                name = _req(f"{sym}.TF-Name")
                if name:
                    found.append(series)
                    logger.info(f"  [OK] {series}  (樣本: {sym} = {name})")
                    break

    return found


def _do_discover():
    from core import taifex_calendar as tc
    center = _get_center_price()
    logger.info(f"掃描所有有效系列（中心點 {center}，120 組測試）...")
    found = _scan_valid_series(center)
    if not found:
        logger.info("未找到任何有效系列（請確認新富邦e01已開啟）")
        return

    # 依結算日排序
    now = datetime.datetime.now()
    def _sort_key(series: str):
        # series 格式：{PREFIX}N{MM}，例如 TX4N03
        n_idx  = series.index('N')
        prefix = series[:n_idx]          # e.g. "TX4"
        month  = int(series[n_idx + 1:]) # e.g. 3
        year   = now.year if month >= now.month else now.year + 1
        sd = tc.settlement_date(prefix, year, month)
        return sd if sd else datetime.date(9999, 1, 1)

    found.sort(key=_sort_key)
    logger.info(f"找到 {len(found)} 個有效系列（依到期日排序）：")
    for series in found:
        n_idx  = series.index('N')
        prefix = series[:n_idx]
        month  = int(series[n_idx + 1:])
        year   = now.year if month >= now.month else now.year + 1
        sd    = tc.settlement_date(prefix, year, month)
        label = tc.tf_name_label(prefix, month)
        logger.info(f"  {series}  ({label})  結算日 {sd}")


# ── --test-ddeml 模式（多執行緒 DDEML 可行性測試，不影響正式流程）──────


def _ddeml_worker(worker_id: int, symbols: list, fields: list,
                  results: dict, errors: dict):
    """
    在獨立 thread 中建立全新的 DDEML 連線，讀取 symbols × fields，
    將讀到的值存入 results[worker_id]，失敗訊息存入 errors[worker_id]。
    """
    inst  = _DWORD(0)
    hconv = None
    cb    = _PFNCALLBACK(_null_cb)   # per-thread callback（避免跨 thread 共用）
    try:
        ret = _user32.DdeInitializeW(ctypes.byref(inst), cb, _APPCMD_CLIENTONLY, 0)
        if ret != 0:
            errors[worker_id] = f"DdeInitializeW 失敗 ret={ret}"
            return

        hsz_svc   = _user32.DdeCreateStringHandleW(inst.value, "XQFAP", _CP_WINUNICODE)
        hsz_topic = _user32.DdeCreateStringHandleW(inst.value, "Quote",  _CP_WINUNICODE)
        hconv     = _user32.DdeConnect(inst.value, hsz_svc, hsz_topic, None)
        _user32.DdeFreeStringHandle(inst.value, hsz_svc)
        _user32.DdeFreeStringHandle(inst.value, hsz_topic)
        if not hconv:
            errors[worker_id] = "DdeConnect 失敗（XQFAP 可能拒絕多連線）"
            _user32.DdeUninitialize(inst.value)
            return

        ok, fail = 0, 0
        sample   = {}
        for sym in symbols:
            for fld in fields:
                item     = f"{sym}.{fld}"
                hsz_item = _user32.DdeCreateStringHandleW(inst.value, item, _CP_WINUNICODE)
                dr       = _DWORD(0)
                hdata    = _user32.DdeClientTransaction(
                    None, 0, hconv, hsz_item,
                    _CF_TEXT, _XTYP_REQUEST, _DDE_TIMEOUT_MS, ctypes.byref(dr)
                )
                _user32.DdeFreeStringHandle(inst.value, hsz_item)
                if hdata:
                    sz  = _user32.DdeGetData(hdata, None, 0, 0)
                    buf = ctypes.create_string_buffer(sz) if sz > 0 else ctypes.create_string_buffer(1)
                    _user32.DdeGetData(hdata, buf, sz, 0)
                    _user32.DdeFreeDataHandle(hdata)
                    val = buf.raw.rstrip(b'\x00').decode('cp950', errors='replace').strip()
                    if val and val != '-':
                        ok += 1
                        if len(sample) < 2:
                            sample[item] = val
                    else:
                        fail += 1
                else:
                    fail += 1

        results[worker_id] = {'ok': ok, 'fail': fail, 'sample': sample}

    except Exception as e:
        errors[worker_id] = str(e)
    finally:
        if hconv:
            try:
                _user32.DdeDisconnect(hconv)
            except Exception:
                pass
        try:
            _user32.DdeUninitialize(inst.value)
        except Exception:
            pass


def _do_test_ddeml():
    """
    --test-ddeml：測試 XQFAP 是否支援多條並行 DDEML 連線。
    使用 pywin32 找一個有效系列，在 N threads 各自建 DDEML 連線並平行讀取。
    結果印至 log，不影響正式輪詢程式。
    """
    NUM_WORKERS = 4
    FIELDS      = ['TF-TotalVolume', 'TF-InOutRatio']

    center = _get_center_price()
    if not center:
        logger.error("無法取得中心價，請確認新富邦e01已開啟")
        return

    # 找一個有效系列
    valid = _scan_valid_series(center)
    if not valid:
        logger.error("找不到有效系列")
        return
    series = valid[0]
    logger.info(f"測試使用系列：{series}，中心價 {center}")

    # 建立測試 symbols（center ±3 strikes × C/P = 12 個）
    strikes = [center + i * STRIKE_STEP for i in range(-3, 3)]
    symbols = [f"{series}{side}{s}" for s in strikes for side in ('C', 'P')]
    logger.info(f"測試 symbols：{len(symbols)} 個（{symbols[0]} … {symbols[-1]}）")

    # 平均分配給 NUM_WORKERS 個 thread
    chunks  = [symbols[i::NUM_WORKERS] for i in range(NUM_WORKERS)]
    results, errors = {}, {}
    threads = []
    t0 = time.time()

    for wid, chunk in enumerate(chunks):
        t = threading.Thread(
            target=_ddeml_worker,
            args=(wid, chunk, FIELDS, results, errors),
            daemon=True,
        )
        threads.append(t)

    logger.info(f"啟動 {NUM_WORKERS} 個 DDEML worker threads...")
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    elapsed = time.time() - t0
    logger.info(f"全部完成，耗時 {elapsed:.2f}s")
    logger.info("─" * 60)

    total_ok, total_fail = 0, 0
    for wid in range(NUM_WORKERS):
        if wid in errors:
            logger.warning(f"  Worker-{wid}: [FAIL] {errors[wid]}")
        elif wid in results:
            r = results[wid]
            total_ok   += r['ok']
            total_fail += r['fail']
            logger.info(
                f"  Worker-{wid}: [OK] ok={r['ok']} fail={r['fail']} "
                f"樣本={r['sample']}"
            )
        else:
            logger.warning(f"  Worker-{wid}: [TIMEOUT] 未在 15s 內完成")

    logger.info("─" * 60)
    if errors:
        logger.warning(f"結論：{len(errors)} 個 worker 失敗 → 多執行緒 DDEML 不可行")
    else:
        logger.info(
            f"結論：{NUM_WORKERS} 條 DDEML 連線全數成功，"
            f"ok={total_ok} fail={total_fail}，多執行緒可行！"
        )


# ── 主程式 ────────────────────────────────────────────────────

def main():
    global _all_metas, _all_prevs, _tracked_full_series, _series_sd
    global _all_valid_series

    # 寫入 PID 檔，供 main.py /api/restart-feed 使用
    _pid_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'monitor', 'xqfap.pid')
    with open(_pid_file, 'w') as _f:
        _f.write(str(os.getpid()))
    atexit.register(lambda: os.remove(_pid_file) if os.path.exists(_pid_file) else None)

    if not _connect_dde():
        sys.exit(1)
    _connect_ddeml()

    if MODE == '--discover':
        _do_discover()
        return

    if MODE == '--test-ddeml':
        _do_test_ddeml()
        return

    center = _get_center_price()

    valid_series = _scan_valid_series(center)
    if not valid_series:
        logger.error("找不到任何有效系列！請確認新富邦e01已開啟。")
        sys.exit(1)

    _all_valid_series = valid_series

    from core import taifex_calendar as tc
    now   = datetime.datetime.now()
    today = now.date()

    # 計算所有 valid_series 的結算日（sd >= today 才保留）
    after_cutoff = now.time() >= datetime.time(14, 30)
    all_with_sd = []
    for series in valid_series:
        n_idx  = series.index('N')
        prefix = series[:n_idx]
        month  = int(series[n_idx + 1:])
        year   = now.year if month >= now.month else now.year + 1
        sd     = tc.settlement_date(prefix, year, month)
        if sd and sd >= today:
            all_with_sd.append((series, sd, str(sd)))

    if not all_with_sd:
        logger.error("找不到有效系列（結算日 >= 今天）！")
        sys.exit(1)

    # 分類：週選 / 月選（依結算日排序）
    weekly_all  = sorted([(s, sd, sd_str) for s, sd, sd_str in all_with_sd if not s.startswith('TXON')], key=lambda x: x[1])
    monthly_all = sorted([(s, sd, sd_str) for s, sd, sd_str in all_with_sd if     s.startswith('TXON')], key=lambda x: x[1])

    # 篩選週選：3個尚未結算 + 若 15:00 後則額外保留今天結算的
    if after_cutoff:
        settling_today   = [(s, sd, sd_str) for s, sd, sd_str in weekly_all if sd == today]
        unsettled_weekly = [(s, sd, sd_str) for s, sd, sd_str in weekly_all if sd >  today][:3]
        selected_weekly  = settling_today + unsettled_weekly
        default_series   = unsettled_weekly[0][0] if unsettled_weekly else None
    else:
        selected_weekly = weekly_all[:3]   # sd >= today，可包含今天結算的
        default_series  = selected_weekly[0][0] if selected_weekly else None

    # 月選同樣以 15:00 為切換點：15:00 後保留當天結算的，再取下一個
    if after_cutoff:
        settling_today_monthly  = [(s,sd,ss) for s,sd,ss in monthly_all if sd == today]
        unsettled_monthly       = [(s,sd,ss) for s,sd,ss in monthly_all if sd >  today][:1]
        selected_monthly        = settling_today_monthly + unsettled_monthly
    else:
        selected_monthly = monthly_all[:1]
    selected_all     = sorted(selected_weekly + selected_monthly, key=lambda x: x[1])

    # default 系列放第一位（main.py 以 i==0 決定 active）
    if default_series:
        selected_all = (
            [(s, sd, sd_str) for s, sd, sd_str in selected_all if s == default_series] +
            [(s, sd, sd_str) for s, sd, sd_str in selected_all if s != default_series]
        )

    series_with_sd = [(s, sd_str) for s, sd, sd_str in selected_all]
    logger.info(f"追蹤系列（週選 {len(selected_weekly)} 個+月選 1 個）：{[s for s, _ in series_with_sd]}")
    logger.info(f"預設主合約：{default_series}")

    _tracked_full_series = [s for s, _ in series_with_sd]
    _all_valid_series    = _tracked_full_series

    # 等 uvicorn 就緒再開始 POST /api/init（避免 race condition）
    for _wait in range(30):
        try:
            _http.get(f"{SERVER_URL}/api/status", timeout=2)
            break
        except Exception:
            logger.info(f"等待 uvicorn 就緒... ({_wait+1}/30)")
            time.sleep(1)

    # 啟動時一次性探索所有系列（advise 需要在 loop 啟動前知道全部合約）
    _all_metas = {}
    _all_prevs = {}
    for i, (full_series, sd_str) in enumerate(series_with_sd):
        _series_sd[full_series] = sd_str
        contracts_full, meta_full = _discover_contracts(center, full_series)
        if not contracts_full:
            logger.warning(f"{full_series} 探索失敗，跳過")
            continue
        day_series    = full_series.replace('N', '')
        contracts_day, meta_day = _build_day_meta(meta_full, full_series)
        _all_metas[full_series] = meta_full
        _all_metas[day_series]  = meta_day
        _all_prevs[full_series] = {}
        _all_prevs[day_series]  = {}
        _post_init(contracts_full, full_series, sd_str)
        _post_init(contracts_day,  day_series,  sd_str)
        _post_contracts(_all_valid_series)   # _post_init 完成即更新前端下拉選單，不等慢速快照
        if i == 0:  # 只對 active series 做初始快照；其餘由 bg_poll 第一輪更新
            _push_snapshot(meta_full, full_series)
            # 立刻開始 DDEML 全量（Phase1 TX1N04 並行於 TX104 push_snapshot）
            threading.Thread(target=_bulk_request_series,
                             args=(full_series,), daemon=True).start()
            _push_snapshot(meta_day,  day_series)
            _push_futures_price(float(center))
        else:
            logger.info(f"跳過 push_snapshot [{full_series}]，立即背景載入")
            threading.Thread(target=_bulk_request_series,
                             args=(full_series,), daemon=True).start()

    if not _all_metas:
        logger.error("所有系列探索失敗！")
        sys.exit(1)

    _rebuild_sym_to_series()

    threading.Thread(target=_auto_reinit_scheduler, daemon=True).start()
    threading.Thread(target=_advise_worker, daemon=True).start()
    threading.Thread(target=_quote_poll_worker, daemon=True).start()
    threading.Thread(target=_switch_listener, daemon=True).start()
    threading.Thread(target=_series_watcher, daemon=True).start()

    # 背景輪詢：每個 full series 一條 thread，錯開時間
    full_series_list = [s for s in _all_metas if 'N' in s]
    n = len(full_series_list)
    for i, fs in enumerate(full_series_list):
        offset = i * (_BG_POLL_INTERVAL / max(n, 1))
        threading.Thread(target=_bg_poll_one_series,
                         args=(fs, offset), daemon=True).start()

    logger.info(
        f"啟動 DDE Advise（push-based，取代輪詢）；"
        f"已追蹤 {len(_all_metas)} 個系列；"
        f"背景輪詢 {n} 個系列（間隔 {_BG_POLL_INTERVAL}s，錯開）"
    )
    _advise_loop()


if __name__ == '__main__':
    main()
