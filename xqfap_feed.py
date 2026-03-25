"""
xqfap_feed.py — 新富邦e01 DDE 橋接 (v2.18)
讀取 XQFAP DDE server 的 InOutRatio + TotalVolume + AvgPrice
推送至 FastAPI server (main.py) /api/init + /api/feed

【架構】
  pywin32 DDE  → Name / TotalVolume / InOutRatio 探索與輪詢（無 item quota 限制）
  DDEML ctypes → AvgPrice 專用（修正 pywin32 小數截斷 bug）

【Progressive Loading】
  啟動時：掃描所有有效系列 → 前 3 個立即探索（fast tier）→ 其餘排入背景佇列（slow tier）
  Fast tier：全日盤每 1s、日盤每 3s
  Slow tier：全日盤每 10s、日盤每 30s
  背景佇列：_poll_loop 主線程每輪處理一個（保留 DDE thread affinity）

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


# ── 主輪詢迴圈 ────────────────────────────────────────────────
# _all_metas:  {series → meta_dict}，包含所有被追蹤系列的 full + day
# _all_prevs:  {series → prev_dict}，用於偵測變動
# _fast_series: fast tier（前 3 系列）的 series key 集合
# _bg_load_queue: 尚未探索的系列 (full_series, sd_str) 佇列
# _all_valid_series: scan 到的全部有效系列（供 _post_contracts 更新 live 旗標）

_all_metas:        dict = {}
_all_prevs:        dict = {}
_fast_series:      set  = set()
_bg_load_queue:    list = []
_all_valid_series: list = []

# 輪詢間隔（單位：tick，1 tick ≈ 1 秒）
_FAST_FULL = 1
_FAST_DAY  = 3
_SLOW_FULL = 10
_SLOW_DAY  = 30

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


def _load_one_series(center: int, full_series: str, sd_str: str):
    """探索並初始化單一系列，加入 _all_metas（慢速 tier）"""
    _series_sd[full_series] = sd_str
    contracts_full, meta_full = _discover_contracts(center, full_series)
    if not contracts_full:
        logger.warning(f"背景載入 {full_series} 失敗，跳過")
        return
    day_series                = full_series.replace('N', '')
    contracts_day, meta_day   = _build_day_meta(meta_full, full_series)
    _post_init(contracts_full, full_series, sd_str)
    _post_init(contracts_day,  day_series,  sd_str)
    _all_metas[full_series]   = meta_full
    _all_metas[day_series]    = meta_day
    _all_prevs[full_series]   = {}
    _all_prevs[day_series]    = {}
    _push_snapshot(meta_full, full_series)
    _push_snapshot(meta_day,  day_series)
    logger.info(f"背景載入完成：{full_series} / {day_series}（slow tier，{_SLOW_FULL}s 更新）")
    _post_contracts(_all_valid_series)  # 通知前端移除 ·


def _fetch_active_series() -> "tuple[str, str]":
    """查詢 main.py 目前 active 系列，失敗回傳空字串"""
    try:
        r = requests.get(f"{SERVER_URL}/api/active-series", timeout=2)
        d = r.json()
        return d.get('full', ''), d.get('day', '')
    except Exception:
        return '', ''


def _poll_loop():
    """
    輪詢所有被追蹤系列。
    Fast tier（前 3 系列）：全日盤每 1s、日盤每 3s。
    Slow tier（背景載入）：全日盤每 10s、日盤每 30s。
    Active series（目前畫面顯示的）：無論 tier，強制每 1s（full）/ 3s（day）。
    背景佇列每輪處理一個（保留 DDE thread affinity）。
    """
    tick         = 0
    series_ticks: dict[str, int] = {}   # series → 上次輪詢的 tick
    active_full  = ''
    active_day   = ''

    while True:
        _reinit_flag.wait(timeout=1.0)
        tick += 1
        if tick % 60 == 0:
            logger.info(f"heartbeat: {[f'{s}={len(m)}' for s, m in _all_metas.items()]}")

        if _reinit_flag.is_set():
            _reinit_flag.clear()
            _reinit()
            series_ticks.clear()
            for s, m in list(_all_metas.items()):
                _push_snapshot(m, s)
            continue

        # 每 5 tick 查一次目前 active 系列（偵測用戶切換合約）
        if tick % 5 == 0:
            active_full, active_day = _fetch_active_series()

        # 背景佇列：每輪探索一個尚未載入的系列
        if _bg_load_queue:
            center = _get_center_price()
            full_series, sd_str = _bg_load_queue.pop(0)
            _load_one_series(center, full_series, sd_str)

        # 輪詢所有已追蹤系列
        for series, meta in list(_all_metas.items()):
            if _reinit_flag.is_set():
                break
            is_full = 'N' in series
            # Active 系列強制 fast rate；其餘依 tier 決定
            is_active = (series == active_full or series == active_day)
            if is_active or series in _fast_series:
                interval = _FAST_FULL if is_full else _FAST_DAY
            else:
                interval = _SLOW_FULL if is_full else _SLOW_DAY
            if tick - series_ticks.get(series, 0) < interval:
                continue
            series_ticks[series] = tick
            batch = _poll_meta(meta, _all_prevs[series])
            if batch:
                _post_feed(batch, series)


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
        logger.info(f"[排程] {now.strftime('%H:%M')} 盤前重新初始化（設旗）...")
        _reinit_flag.set()


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
    掃描 XQFAP 中所有有效系列。
    - 當月 + 下個月：10 個前綴全掃
    - 第3個月 ～ 第12個月：只掃 TXO（月選）
    共 120 組測試，約 0.5 秒。
    回傳有效系列碼清單，如 ['TX4N03','TXYN03','TX1N04',...]
    """
    now   = datetime.datetime.now()
    found = []

    # 測試點：center, +50, +100, +150（其中一個必中）
    test_strikes = [center, center + 50, center + 100, center + 150]

    for month_offset in range(12):
        dt     = now + datetime.timedelta(days=month_offset * 31)
        month  = dt.strftime('%m')
        # 第3個月（offset>=2）以後只掃月選
        prefixes = _ALL_PREFIXES if month_offset < 2 else ['TXO']

        for prefix in prefixes:
            series = f"{prefix}N{month}"
            for strike in test_strikes:
                sym  = f"{series}C{strike}"
                name = _req(f"{sym}.TF-Name")
                if name:
                    found.append(series)
                    logger.info(f"  [OK] {series}  (樣本: {sym} = {name})")
                    break  # 這個系列確認有效，不需再測更多點

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


# ── 主程式 ────────────────────────────────────────────────────

def main():
    global _all_metas, _all_prevs, _tracked_full_series, _series_sd
    global _fast_series, _bg_load_queue, _all_valid_series

    if not _connect_dde():
        sys.exit(1)
    _connect_ddeml()

    if MODE == '--discover':
        _do_discover()
        return

    center = _get_center_price()

    # 掃描所有有效系列（含結算日排序）
    valid_series = _scan_valid_series(center)
    if not valid_series:
        logger.error("找不到任何有效系列！請確認新富邦e01已開啟。")
        sys.exit(1)

    _all_valid_series = valid_series  # 供 _load_one_series 呼叫 _post_contracts 使用

    import taifex_calendar as tc
    now   = datetime.datetime.now()
    today = now.date()

    # 計算各系列結算日，過濾已到期
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

    # 前 3 個系列立即探索（fast tier），其餘排入背景佇列（slow tier）
    fast_list      = series_with_sd[:3]
    _bg_load_queue = list(series_with_sd[3:])

    logger.info(f"立即追蹤（fast tier，前{len(fast_list)}個）：{[s for s, _ in fast_list]}")
    if _bg_load_queue:
        logger.info(f"背景排程（slow tier，共{len(_bg_load_queue)}個）：{[s for s, _ in _bg_load_queue]}")

    _tracked_full_series = [s for s, _ in series_with_sd]

    # 立即探索 fast 系列
    _all_metas = {}
    _all_prevs = {}
    for full_series, sd_str in fast_list:
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
        _fast_series.add(full_series)
        _fast_series.add(day_series)
        _post_init(contracts_full, full_series, sd_str)
        _post_init(contracts_day,  day_series,  sd_str)
        _push_snapshot(meta_full, full_series)
        _push_snapshot(meta_day,  day_series)

    if not _all_metas:
        logger.error("所有系列探索失敗！")
        sys.exit(1)

    # 推送合約下拉清單（fast 系列 live=True，slow 系列 live=False 顯示 ·）
    _post_contracts(valid_series)

    threading.Thread(target=_auto_reinit_scheduler, daemon=True).start()

    logger.info(
        f"開始輪詢 fast={sorted(_fast_series)}；"
        f"背景佇列 {len(_bg_load_queue)} 個系列待載入..."
    )
    _poll_loop()


if __name__ == '__main__':
    main()
