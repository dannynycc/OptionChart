"""
xqfap_feed.py — 新富邦e01 DDE 橋接 (v2.11)
讀取 XQFAP DDE server 的 InOutRatio + TotalVolume + AvgPrice
推送至 FastAPI server (main.py) /api/init + /api/feed

【架構】
  pywin32 DDE  → Name / TotalVolume / InOutRatio 探索與輪詢（無 item quota 限制）
  DDEML ctypes → AvgPrice 專用（修正 pywin32 小數截斷 bug）

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

def _null_cb(a, b, c, d, e, f, g, h):
    return None
_dde_callback  = _PFNCALLBACK(_null_cb)
_ddeml_inst    = _DWORD(0)
_ddeml_hconv   = None


def _connect_ddeml() -> bool:
    """建立 DDEML 連線（僅用於讀 AvgPrice）"""
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
        logger.info("DDEML 連線成功（AvgPrice 專用）")
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

def _discover_contracts(center: int) -> "tuple[list, dict]":
    """
    pywin32 DDE 探索：先從 center 往上，再從 center-50 往下。
    pywin32 無 item quota 限制，可探索完整 27300～37800 範圍。
    """
    series = XQ_SERIES
    logger.info(
        f"探索合約：TX4{series} 系列，從 {center} 向兩側展開"
        f"（連續{MISS_LIMIT}個miss即停）"
    )

    found: dict[int, dict[str, bool]] = {}

    def _probe_direction(start: int, step: int):
        miss   = 0
        strike = start
        while miss < MISS_LIMIT:
            hit = False
            for side in ('C', 'P'):
                symbol = f"TX4{series}{side}{strike}"
                name   = _req(f"{symbol}.TF-Name")
                if name:
                    found.setdefault(strike, {})[side] = True
                    hit = True
            miss = 0 if hit else miss + 1
            strike += step

    _probe_direction(center,              +STRIKE_STEP)   # UP
    _probe_direction(center - STRIKE_STEP, -STRIKE_STEP)  # DN（不重複 center）

    contracts, meta = [], {}
    found_c = found_p = 0
    for strike in sorted(found.keys()):
        for side in ('C', 'P'):
            if not found[strike].get(side):
                continue
            symbol = f"TX4{series}{side}{strike}"
            contracts.append({'symbol': symbol, 'strike': strike,
                               'side': side, 'prev_close': 0.0})
            meta[symbol] = {'strike': strike, 'side': side}
            if side == 'C':
                found_c += 1
            else:
                found_p += 1

    if found:
        strikes = sorted(found.keys())
        logger.info(
            f"探索完成：Call {found_c} 個，Put {found_p} 個，共 {len(contracts)} 個合約"
            f"（{strikes[0]}～{strikes[-1]}）"
        )
    return contracts, meta


# ── HTTP 推送 ─────────────────────────────────────────────────

def _post_init(contracts: list, mode: str = "full"):
    try:
        r = requests.post(
            f"{SERVER_URL}/api/init",
            json={'settlement_date': SETTLEMENT_DATE, 'contracts': contracts, 'mode': mode},
            timeout=10,
        )
        logger.info(f"POST /api/init [{mode}] → HTTP {r.status_code}，{len(contracts)} 個合約")
    except Exception as e:
        logger.error(f"POST /api/init 失敗：{e}")


def _post_feed(batch: list, mode: str = "full"):
    try:
        r = requests.post(f"{SERVER_URL}/api/feed?mode={mode}", json=batch, timeout=5)
        if r.status_code != 200:
            logger.warning(f"POST /api/feed [{mode}] HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"POST /api/feed 失敗：{e}")


# ── 初始快照 ──────────────────────────────────────────────────

def _push_snapshot(meta: dict, mode: str = "full"):
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
        _post_feed(snapshot, mode=mode)
        logger.info(f"初始快照推送 [{mode}]：{len(snapshot)} 筆")


# ── 主輪詢迴圈 ────────────────────────────────────────────────

_meta_full: dict = {}
_meta_day:  dict = {}
_prev_full: dict = {}
_prev_day:  dict = {}

_reinit_flag = threading.Event()


def _poll_meta(meta: dict, prev: dict) -> list:
    batch = []
    for symbol in list(meta.keys()):
        if _reinit_flag.is_set():
            break
        try:
            data = _get_fields(symbol)
        except Exception:
            logger.warning("DDE 讀取異常，嘗試重連...")
            if _connect_dde():
                logger.info("DDE 重連成功")
            break
        if data is None:
            continue
        new_vol   = int(data['total_volume'])
        new_ratio = data['inout_ratio']
        new_avg   = data['avg_price']
        old = prev.get(symbol)
        if old is None or (new_ratio != old[0] or new_vol != old[1]):
            batch.append({
                'symbol':       symbol,
                'trade_volume': new_vol,
                'inout_ratio':  new_ratio,
                'avg_price':    new_avg,
            })
            prev[symbol] = (new_ratio, new_vol)
    return batch


def _poll_loop():
    """永遠同時維護 full + day；full 每輪，day 每 3 輪"""
    tick     = 0
    day_tick = 0
    while True:
        _reinit_flag.wait(timeout=1.0)
        tick += 1
        if tick % 60 == 0:
            logger.info(f"heartbeat: full={len(_meta_full)} day={len(_meta_day)} 合約")

        if _reinit_flag.is_set():
            _reinit_flag.clear()
            _reinit()
            _push_snapshot(_meta_full, "full")
            _push_snapshot(_meta_day,  "day")
            day_tick = 0
            continue

        batch_full = _poll_meta(_meta_full, _prev_full)
        if batch_full:
            _post_feed(batch_full, mode="full")

        day_tick += 1
        if day_tick % 3 == 0 and not _reinit_flag.is_set():
            batch_day = _poll_meta(_meta_day, _prev_day)
            if batch_day:
                _post_feed(batch_day, mode="day")


# ── 自動重新初始化排程 ────────────────────────────────────────

_REINIT_TIMES    = {(8, 43), (14, 58)}
_last_reinit_key = ""


def _build_day_meta(meta_full: dict) -> "tuple[list, dict]":
    full_prefix = f"TX4{XQ_SERIES}"
    day_prefix  = f"TX4{XQ_SERIES[1:]}"
    contracts, meta = [], {}
    for old_sym, info in meta_full.items():
        suffix = old_sym[len(full_prefix):]
        sym = f"{day_prefix}{suffix}"
        contracts.append({'symbol': sym, 'strike': info['strike'],
                           'side': info['side'], 'prev_close': 0.0})
        meta[sym] = {'strike': info['strike'], 'side': info['side']}
    return contracts, meta


def _reinit():
    global _meta_full, _meta_day, _prev_full, _prev_day
    center = _get_center_price()
    contracts_full, new_meta_full = _discover_contracts(center)
    if not contracts_full:
        logger.error("重新初始化失敗：找不到任何合約")
        return
    contracts_day, new_meta_day = _build_day_meta(new_meta_full)
    _post_init(contracts_full, mode="full")
    _post_init(contracts_day,  mode="day")
    _meta_full = new_meta_full
    _meta_day  = new_meta_day
    _prev_full = {}
    _prev_day  = {}
    logger.info("重新初始化完成（full + day）")


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
        logger.info(f"[排程] {now.strftime('%H:%M')} 盤前重新初始化（設旗）...")
        _reinit_flag.set()


# ── --discover 模式 ───────────────────────────────────────────

def _do_discover():
    month = datetime.datetime.now().strftime('%m')
    logger.info(f"搜尋本月 ({month}) 所有可用系列碼...")
    found_any = False
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        series = f"{letter}{month}"
        for test_strike in (32000, 32500, 33000, 31500):
            sym  = f"TX4{series}C{test_strike}"
            name = _req(f"{sym}.TF-Name")
            if name:
                logger.info(f"  [OK] 系列碼 {series!r}  (樣本: {sym} = {name})")
                found_any = True
                break
    if not found_any:
        logger.info("  未找到任何有效系列碼（請確認新富邦e01已開啟且合約已加載）")


# ── 主程式 ────────────────────────────────────────────────────

def main():
    global _meta_full, _meta_day

    if not _connect_dde():
        sys.exit(1)

    # DDEML 連線（AvgPrice 專用，失敗不影響主流程）
    _connect_ddeml()

    if MODE == '--discover':
        _do_discover()
        return

    center = _get_center_price()
    contracts_full, _meta_full = _discover_contracts(center)

    if not contracts_full:
        logger.error(
            f"找不到任何合約！請確認 config_xqfap.py 的 XQ_SERIES（目前={XQ_SERIES!r}）。\n"
            f"可執行 python xqfap_feed.py --discover 找出正確的系列碼。"
        )
        sys.exit(1)

    contracts_day, _meta_day = _build_day_meta(_meta_full)

    _post_init(contracts_full, mode="full")
    _post_init(contracts_day,  mode="day")
    _push_snapshot(_meta_full, "full")
    _push_snapshot(_meta_day,  "day")

    threading.Thread(target=_auto_reinit_scheduler, daemon=True).start()

    logger.info(
        f"開始輪詢 full={len(_meta_full)} + day={len(_meta_day)} 合約"
        f"（TX4{XQ_SERIES} / TX4{XQ_SERIES[1:]}）..."
    )
    _poll_loop()


if __name__ == '__main__':
    main()
