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
import time
import ctypes
import logging
import threading
import datetime
import queue
import ctypes.wintypes

import win32ui  # noqa: F401 — dde 依賴它
import dde

try:
    import requests
except ImportError:
    print("請先安裝 requests：pip install requests")
    sys.exit(1)

try:
    import config_xqfap as cfg
except ImportError:
    print("找不到 config_xqfap.py，請從 config_xqfap_template.py 複製並填入設定")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── 設定 ──────────────────────────────────────────────────────

XQ_SERIES       = cfg.XQ_SERIES
SETTLEMENT_DATE = cfg.SETTLEMENT_DATE
SERVER_URL      = getattr(cfg, 'SERVER_URL', 'http://localhost:8000')

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
_WM_TIMER            = 0x0113   # Windows WM_TIMER
_WATCHDOG_TIMER_ID   = 1        # SetTimer ID
_WATCHDOG_INTERVAL   = 30000    # 30 秒觸發一次 WM_TIMER（ms）
_WATCHDOG_THRESHOLD  = 60.0     # 超過 60 秒無 callback → 重新連線
_RESUBSCRIBE_COOLDOWN = 120.0   # 重新訂閱最短冷卻時間（秒）

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
    buf = ctypes.create_string_buffer(256)
    _user32.DdeQueryStringW(_ddeml_inst.value, hsz2, buf, 256, 1004)   # 1004=CP_WINANSI
    item_full = buf.value.decode('cp950', errors='replace').strip()
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
    global _last_callback_time
    _last_callback_time = time.time()
    series = _sym_to_series.get(symbol)
    if series:
        try:
            _change_queue.put_nowait((series, symbol, new_vol))
        except queue.Full:
            pass
    return _DDE_FACK


_dde_callback         = _PFNCALLBACK(_advise_cb_fn)
_ddeml_inst           = _DWORD(0)
_ddeml_hconv          = None
_change_queue         = queue.Queue(maxsize=10000)
_sym_to_series: dict  = {}   # full-series symbol → series（e.g. 'TXYN03C33000' → 'TXYN03'）
_advise_loop_tid      = 0    # Win32 thread ID of advise message loop
_pending_subscribe: list = []
_pending_subscribe_lock   = threading.Lock()
_last_callback_time: float = 0.0   # 最後收到 ADVDATA 的時間
_last_resubscribe_time: float = 0.0  # 最後重新訂閱的時間


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
        sz  = _user32.DdeGetData(hdata, None, 0, 0)
        buf = ctypes.create_string_buffer(sz) if sz > 0 else ctypes.create_string_buffer(1)
        _user32.DdeGetData(hdata, buf, sz, 0)
        _user32.DdeFreeDataHandle(hdata)
        val = buf.raw.rstrip(b'\x00').decode('cp950', errors='replace').strip()
        return '' if val == '-' else val
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
            _thread_local.hconv = None   # 連線可能已斷，下次觸發重連
            return ''
        sz  = _user32.DdeGetData(hdata, None, 0, 0)
        buf = ctypes.create_string_buffer(sz) if sz > 0 else ctypes.create_string_buffer(1)
        _user32.DdeGetData(hdata, buf, sz, 0)
        _user32.DdeFreeDataHandle(hdata)
        val = buf.raw.rstrip(b'\x00').decode('cp950', errors='replace').strip()
        return '' if val in ('-', '') else val.rstrip('%')
    except Exception:
        _thread_local.hconv = None
        return ''




def _get_center_price() -> int:
    val   = _req("FITX00.TF-Price")
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
    sd = settlement_date or SETTLEMENT_DATE
    try:
        r = requests.post(
            f"{SERVER_URL}/api/init",
            json={'settlement_date': sd, 'contracts': contracts, 'series': series},
            timeout=10,
        )
        logger.info(f"POST /api/init [{series}] → HTTP {r.status_code}，{len(contracts)} 個合約")
    except Exception as e:
        logger.error(f"POST /api/init 失敗：{e}")


def _post_feed(batch: list, series: str):
    try:
        r = requests.post(f"{SERVER_URL}/api/feed?series={series}", json=batch, timeout=5)
        if r.status_code != 200:
            logger.warning(f"POST /api/feed [{series}] HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"POST /api/feed 失敗：{e}")


# ── 初始快照 ──────────────────────────────────────────────────

def _push_snapshot(meta: dict, series: str):
    snapshot = []
    for symbol in meta:
        data = _get_fields(symbol)
        if data is None:
            continue
        snapshot.append({
            'symbol':       symbol,
            'trade_volume': int(data['total_volume']),
            'inout_ratio':  data['inout_ratio'],
            'avg_price':    data['avg_price'],
        })
    if snapshot:
        _post_feed(snapshot, series=series)
        logger.info(f"初始快照推送 [{series}]：{len(snapshot)} 筆")


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
    logger.info(f"載入完成：{full_series} / {day_series}")
    # 通知 advise 迴圈訂閱新系列
    with _pending_subscribe_lock:
        _pending_subscribe.extend(list(meta_full.keys()))
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
        hdata = _user32.DdeClientTransaction(
            None, 0, _ddeml_hconv, hsz,
            _CF_TEXT, _XTYP_ADVSTART, _TIMEOUT_ASYNC, None,
        )
        _user32.DdeFreeStringHandle(_ddeml_inst.value, hsz)
        if hdata:
            _user32.DdeFreeDataHandle(hdata)
            ok += 1
        else:
            fail += 1
    logger.info(f"ADVSTART：{ok} 成功，{fail} 失敗（共 {len(symbols)} 個合約）")


_BATCH_WINDOW = 0.5   # 秒：每 0.5s 批次 REQUEST ratio/avg 並推送


def _advise_worker():
    """
    批次 worker：收集 _change_queue 的 TotalVolume 變動，
    每 _BATCH_WINDOW 秒 REQUEST InOutRatio + AvgPrice，批次推送 FastAPI。
    """
    logger.info("advise worker 啟動")
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

        by_series: dict = {}
        for (series, symbol), new_vol in changed.items():
            ratio_str = _req_thread(f"{symbol}.TF-InOutRatio")
            avg_str   = _req_thread(f"{symbol}.TF-AvgPrice")
            new_ratio = _to_float(ratio_str)
            new_avg   = _to_float(avg_str)
            prev = _all_prevs.get(series)
            if prev is not None:
                prev[symbol] = (new_ratio, new_vol)
            by_series.setdefault(series, []).append({
                'symbol':       symbol,
                'trade_volume': new_vol,
                'inout_ratio':  new_ratio,
                'avg_price':    new_avg,
            })

        for series, batch in by_series.items():
            _post_feed(batch, series)
            # 同步推送對應 day series（e.g. TXYN03 → TXY03）
            day_series = series.replace('N', '')
            if day_series in _all_metas and day_series != series:
                day_batch = [
                    {**item, 'symbol': item['symbol'].replace(series, day_series, 1)}
                    for item in batch
                ]
                _post_feed(day_batch, day_series)


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
    # 只重訂第一個 full series（負載最小）
    first_series = next((s for s in _all_metas if 'N' in s), None)
    if first_series:
        syms = list(_all_metas[first_series].keys())
        _advise_subscribe(syms)
        logger.info(f"[watchdog] 重新訂閱完成（{first_series}，{len(syms)} 個）")


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

    # 只訂閱第一個（最近到期）full series，降低 daqFAP 負載
    first_series = next((s for s in _all_metas if 'N' in s), None)
    if first_series:
        syms = list(_all_metas[first_series].keys())
        _advise_subscribe(syms)
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

        hb_tick += 1
        if hb_tick % 600 == 0:
            elapsed_since_cb = time.time() - _last_callback_time
            total = sum(len(v) for v in _all_prevs.values())
            logger.info(f"heartbeat: {[f'{s}={len(m)}' for s, m in _all_metas.items()]} "
                        f"prev={total} last_cb={elapsed_since_cb:.0f}s ago")

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
        sd = _series_sd.get(full_series, SETTLEMENT_DATE)
        _post_init(contracts_full, full_series, sd)
        _post_init(contracts_day,  day_series,  sd)
        new_metas[full_series] = meta_full
        new_metas[day_series]  = meta_day
        new_prevs[full_series] = {}
        new_prevs[day_series]  = {}
    _all_metas = new_metas
    _all_prevs = new_prevs
    logger.info(f"重新初始化完成：{list(_all_metas.keys())}")


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
    import taifex_calendar as tc
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
        requests.post(f"{SERVER_URL}/api/contracts",
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
        dt    = now + datetime.timedelta(days=month_offset * 31)
        month = dt.strftime('%m')
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
    import taifex_calendar as tc
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
            pass   # DdeDisconnect 可選，process 結束時自動清理
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

    import taifex_calendar as tc
    now   = datetime.datetime.now()
    today = now.date()

    series_with_sd = []
    for series in valid_series:
        n_idx  = series.index('N')
        prefix = series[:n_idx]
        month  = int(series[n_idx + 1:])
        year   = now.year if month >= now.month else now.year + 1
        sd     = tc.settlement_date(prefix, year, month)
        if sd and sd >= today:
            series_with_sd.append((series, str(sd)))

    if not series_with_sd:
        logger.error("找不到有效系列（結算日 >= 今天）！")
        sys.exit(1)

    # 篩選：最近 3 個週選 + 最近 1 個月選
    weekly  = [(s, sd) for s, sd in series_with_sd if not s.startswith('TXON')][:3]
    monthly = [(s, sd) for s, sd in series_with_sd if s.startswith('TXON')][:1]
    series_with_sd = sorted(weekly + monthly, key=lambda x: x[1])
    logger.info(f"追蹤系列（3週+1月）：{[s for s, _ in series_with_sd]}")

    _tracked_full_series = [s for s, _ in series_with_sd]
    _all_valid_series    = _tracked_full_series

    # 啟動時一次性探索所有系列（advise 需要在 loop 啟動前知道全部合約）
    _all_metas = {}
    _all_prevs = {}
    for full_series, sd_str in series_with_sd:
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
        _push_snapshot(meta_full, full_series)
        _push_snapshot(meta_day,  day_series)
        _post_contracts(_all_valid_series)

    if not _all_metas:
        logger.error("所有系列探索失敗！")
        sys.exit(1)

    _rebuild_sym_to_series()

    threading.Thread(target=_auto_reinit_scheduler, daemon=True).start()
    threading.Thread(target=_advise_worker, daemon=True).start()

    logger.info(
        f"啟動 DDE Advise（push-based，取代輪詢）；"
        f"已追蹤 {len(_all_metas)} 個系列"
    )
    _advise_loop()


if __name__ == '__main__':
    main()
