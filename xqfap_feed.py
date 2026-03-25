"""
xqfap_feed.py — 新富邦e01 DDE 橋接
讀取 XQFAP DDE server 的 OutSize/InSize（外盤/內盤口數）+ TotalVolume + AvgPrice
推送至 FastAPI server (main.py) /api/init + /api/feed

【執行環境】Windows 原生 Python（需 pywin32：pip install pywin32）
【必要條件】新富邦e01 軟體必須開著（它是 DDE server）
【執行方式】
  python xqfap_feed.py              # 正常執行
  python xqfap_feed.py --discover   # 列出本月所有可用系列碼

【config_xqfap.py 欄位】
  XQ_SERIES       = "N03"           # 每週換倉時更新（N=W4, 03=March）
  SETTLEMENT_DATE = "20260325"      # 結算日
  SERVER_URL      = "http://localhost:8000"
"""

import sys
import time
import logging
import threading
import datetime

import win32ui  # noqa: F401 — 必須先 import，dde 依賴它
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

XQ_SERIES       = cfg.XQ_SERIES        # e.g. "N03"
SETTLEMENT_DATE = cfg.SETTLEMENT_DATE  # e.g. "20260326"
SERVER_URL      = getattr(cfg, 'SERVER_URL', 'http://localhost:8000')

STRIKE_STEP     = 50
MISS_LIMIT      = 10     # 連續找不到幾個就停止往該方向探索

MODE = sys.argv[1] if len(sys.argv) > 1 else ''

# ── DDE 連線 ──────────────────────────────────────────────────

_srv  = None
_conv = None


def _connect_dde() -> bool:
    """建立 XQFAP DDE 連線，成功回傳 True"""
    global _srv, _conv
    try:
        _srv = dde.CreateServer()
        _srv.Create("XQFAPFeed")
        _conv = dde.CreateConversation(_srv)
        _conv.ConnectTo("XQFAP", "Quote")
        logger.info("XQFAP DDE 連線成功")
        return True
    except Exception as e:
        logger.error(f"XQFAP DDE 連線失敗：{e}")
        logger.error("請確認新富邦e01 軟體已開啟")
        return False


def _request(item: str) -> str:
    """DDE Request，失敗或回傳 '-' 均視為無效，回傳空字串"""
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
        return float(s)
    except (ValueError, TypeError):
        return 0.0


# ── 合約資料讀取 ───────────────────────────────────────────────

def _get_fields(symbol: str) -> "dict | None":
    """
    讀取單一合約的所有欄位。
    Name 無效 → 此合約不存在 → 回傳 None。
    InOutRatio = OutSize/TotalVolume×100（XQFAP 定義，含開盤競價），是主要欄位。
    """
    name = _request(f"{symbol}.TF-Name")
    if not name:
        return None
    return {
        'total_volume': _to_float(_request(f"{symbol}.TF-TotalVolume")),
        'inout_ratio':  _to_float(_request(f"{symbol}.TF-InOutRatio")),
        'avg_price':    _to_float(_request(f"{symbol}.TF-AvgPrice")),
    }


def _get_center_price() -> int:
    """從 FITX00 取得台指期近月現價，作為履約價搜尋中心"""
    val = _request("FITX00.TF-Price")
    price = _to_float(val)
    if price > 0:
        center = int(round(price / STRIKE_STEP) * STRIKE_STEP)
        logger.info(f"台指期現價 {price:.1f}，履約價搜尋中心 {center}")
        return center
    logger.warning("無法取得台指期現價，使用預設中心 32000")
    return 32000


# ── 合約探索 ──────────────────────────────────────────────────

def _discover_contracts(center: int) -> "tuple[list, dict]":
    """
    從 center 向上/向下探索，Call + Put 各自連續 MISS_LIMIT 個找不到就停。
    不寫死範圍，自動適應任何指數位置。
    回傳 (contracts_for_init, meta_map)
    """
    series = XQ_SERIES
    logger.info(f"探索合約：TX4{series} 系列，從 {center} 向兩側展開（連續{MISS_LIMIT}個miss即停）")

    found: dict[int, dict[str, bool]] = {}  # strike -> {'C': bool, 'P': bool}

    def _probe_direction(start: int, step: int):
        """step=+50 往上，step=-50 往下"""
        miss = 0
        strike = start
        while miss < MISS_LIMIT:
            hit = False
            for side_letter in ('C', 'P'):
                symbol = f"TX4{series}{side_letter}{strike}"
                name   = _request(f"{symbol}.TF-Name")
                if name:
                    if strike not in found:
                        found[strike] = {}
                    found[strike][side_letter] = True
                    hit = True
            miss = 0 if hit else miss + 1
            strike += step

    _probe_direction(center, +STRIKE_STEP)
    _probe_direction(center - STRIKE_STEP, -STRIKE_STEP)  # 往下不重複 center

    contracts = []
    meta      = {}
    found_c   = 0
    found_p   = 0

    for strike in sorted(found.keys()):
        for side_letter, side_code in (('C', 'C'), ('P', 'P')):
            if not found[strike].get(side_letter):
                continue
            symbol = f"TX4{series}{side_letter}{strike}"
            contracts.append({
                'symbol':     symbol,
                'strike':     strike,
                'side':       side_code,
                'prev_close': 0.0,
            })
            meta[symbol] = {'strike': strike, 'side': side_code}
            if side_code == 'C':
                found_c += 1
            else:
                found_p += 1

    logger.info(f"探索完成：Call {found_c} 個，Put {found_p} 個，共 {len(contracts)} 個合約")
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

# 背景 thread 設旗，主 thread (_poll_loop) 偵測後在主 thread 做 reinit
# （DDE 有 thread affinity，只能在建立 _conv 的 thread 上 Request）
_reinit_flag = threading.Event()


def _poll_meta(meta: dict, prev: dict) -> list:
    """輪詢 meta 中所有合約，回傳有變動的批次；途中若旗標被設則提前中斷"""
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
    """永遠同時維護 full + day 兩組資料；full 每輪輪詢，day 每 3 輪輪詢一次"""
    tick = 0
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

        # 永遠輪詢 full
        batch_full = _poll_meta(_meta_full, _prev_full)
        if batch_full:
            _post_feed(batch_full, mode="full")

        # day 每 3 輪輪詢一次（節省 DDE 資源，inactive 時略為落後可接受）
        day_tick += 1
        if day_tick % 3 == 0 and not _reinit_flag.is_set():
            batch_day = _poll_meta(_meta_day, _prev_day)
            if batch_day:
                _post_feed(batch_day, mode="day")


# ── 自動重新初始化排程 ────────────────────────────────────────

_REINIT_TIMES    = {(8, 43), (14, 58)}
_last_reinit_key = ""


def _build_day_meta(meta_full: dict) -> "tuple[list, dict]":
    """從 full meta 快速推導 day meta（TX4N03→TX403），不需重新 DDE explore"""
    full_prefix = f"TX4{XQ_SERIES}"          # e.g. "TX4N03"
    day_prefix  = f"TX4{XQ_SERIES[1:]}"      # e.g. "TX403"
    contracts, meta = [], {}
    for old_sym, info in meta_full.items():
        suffix = old_sym[len(full_prefix):]   # e.g. "C32600"
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
    """列出本月所有可用系列碼，幫助換週後找對的 XQ_SERIES"""
    month = datetime.datetime.now().strftime('%m')
    logger.info(f"搜尋本月 ({month}) 所有可用系列碼（以 TX4 + ? + {month} 格式探索）...")
    found_any = False
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        series = f"{letter}{month}"
        # 用 32000 測試，不管 strike 是否存在，只要 Name 有值就代表系列有效
        for test_strike in (32000, 32500, 33000, 31500):
            sym = f"TX4{series}C{test_strike}"
            name = _request(f"{sym}.TF-Name")
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

    if MODE == '--discover':
        _do_discover()
        return

    # 探索 full 系列合約
    center = _get_center_price()
    contracts_full, _meta_full = _discover_contracts(center)

    if not contracts_full:
        logger.error(
            f"找不到任何合約！請確認 config_xqfap.py 的 XQ_SERIES（目前={XQ_SERIES!r}）。\n"
            f"可執行 python xqfap_feed.py --discover 找出正確的系列碼。"
        )
        sys.exit(1)

    # Day 系列由 full 推導，不需重新 DDE 探索
    contracts_day, _meta_day = _build_day_meta(_meta_full)

    # 推送兩個 init
    _post_init(contracts_full, mode="full")
    _post_init(contracts_day,  mode="day")

    # 推送兩個初始快照
    _push_snapshot(_meta_full, "full")
    _push_snapshot(_meta_day,  "day")

    # 啟動自動重新初始化排程
    threading.Thread(target=_auto_reinit_scheduler, daemon=True).start()

    # 啟動主輪詢迴圈（阻塞）
    logger.info(f"開始輪詢 full={len(_meta_full)} + day={len(_meta_day)} 合約（TX4{XQ_SERIES} / TX4{XQ_SERIES[1:]}）...")
    _poll_loop()


if __name__ == '__main__':
    main()
