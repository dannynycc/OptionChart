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
  SETTLEMENT_DATE = "20260326"      # 結算日
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
STRIKE_HALF     = 3500   # 中心 ± 3500 探索

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
    """
    name = _request(f"{symbol}.TF-Name")
    if not name:
        return None
    return {
        'out_size':     _to_float(_request(f"{symbol}.TF-OutSize")),
        'in_size':      _to_float(_request(f"{symbol}.TF-InSize")),
        'total_volume': _to_float(_request(f"{symbol}.TF-TotalVolume")),
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
    在 center ± STRIKE_HALF 範圍內探索有效合約。
    回傳 (contracts_for_init, meta_map)
    """
    lo = center - STRIKE_HALF
    hi = center + STRIKE_HALF
    logger.info(f"探索合約：TX4{XQ_SERIES} 系列，履約價 {lo}~{hi}，step={STRIKE_STEP}")

    contracts = []
    meta      = {}
    found_c   = 0
    found_p   = 0

    for strike in range(lo, hi + 1, STRIKE_STEP):
        for side_letter, side_code in (('C', 'C'), ('P', 'P')):
            symbol = f"TX4{XQ_SERIES}{side_letter}{strike}"
            name   = _request(f"{symbol}.TF-Name")
            if not name:
                continue
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

def _post_init(contracts: list):
    try:
        r = requests.post(
            f"{SERVER_URL}/api/init",
            json={'settlement_date': SETTLEMENT_DATE, 'contracts': contracts},
            timeout=10,
        )
        logger.info(f"POST /api/init → HTTP {r.status_code}，{len(contracts)} 個合約")
    except Exception as e:
        logger.error(f"POST /api/init 失敗：{e}")


def _post_feed(batch: list):
    try:
        r = requests.post(f"{SERVER_URL}/api/feed", json=batch, timeout=5)
        if r.status_code != 200:
            logger.warning(f"POST /api/feed HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"POST /api/feed 失敗：{e}")


# ── 初始快照 ──────────────────────────────────────────────────

def _push_snapshot(meta: dict):
    snapshot = []
    for symbol in meta:
        data = _get_fields(symbol)
        if data is None:
            continue
        snapshot.append({
            'symbol':       symbol,
            'bid_match':    int(data['out_size']),
            'ask_match':    int(data['in_size']),
            'trade_volume': int(data['total_volume']),
            'avg_price':    data['avg_price'],
        })
    if snapshot:
        _post_feed(snapshot)
        logger.info(f"初始快照推送：{len(snapshot)} 筆")


# ── 主輪詢迴圈 ────────────────────────────────────────────────

_meta: dict = {}
_prev: dict = {}


def _poll_loop():
    """每 1 秒輪詢所有合約，有變動才推送"""
    global _meta, _prev
    tick = 0
    while True:
        time.sleep(1.0)
        tick += 1
        if tick % 60 == 0:
            logger.info(f"heartbeat: {len(_meta)} 個合約監視中")

        batch = []
        for symbol, m in list(_meta.items()):
            try:
                data = _get_fields(symbol)
            except Exception:
                # DDE 連線中斷 → 嘗試重連
                logger.warning("DDE 讀取異常，嘗試重連...")
                if _connect_dde():
                    logger.info("DDE 重連成功")
                break

            if data is None:
                continue

            new_bid = int(data['out_size'])
            new_ask = int(data['in_size'])
            new_vol = int(data['total_volume'])
            new_avg = data['avg_price']

            old = _prev.get(symbol)
            if old is None or (
                new_bid != old[0] or
                new_ask != old[1] or
                new_vol != old[2]
            ):
                batch.append({
                    'symbol':       symbol,
                    'bid_match':    new_bid,
                    'ask_match':    new_ask,
                    'trade_volume': new_vol,
                    'avg_price':    new_avg,
                })
                _prev[symbol] = (new_bid, new_ask, new_vol)

        if batch:
            _post_feed(batch)


# ── 自動重新初始化排程 ────────────────────────────────────────

_REINIT_TIMES   = {(8, 43), (14, 58)}
_last_reinit_key = ""


def _reinit():
    global _meta, _prev
    center    = _get_center_price()
    contracts, new_meta = _discover_contracts(center)
    if not contracts:
        logger.error("重新初始化失敗：找不到任何合約")
        return
    _post_init(contracts)
    _meta = new_meta
    _prev = {}
    _push_snapshot(_meta)
    logger.info("重新初始化完成")


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
        threading.Thread(target=_reinit, daemon=True).start()


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
    global _meta, _prev

    if not _connect_dde():
        sys.exit(1)

    if MODE == '--discover':
        _do_discover()
        return

    # 探索合約
    center    = _get_center_price()
    contracts, _meta = _discover_contracts(center)

    if not contracts:
        logger.error(
            f"找不到任何合約！請確認 config_xqfap.py 的 XQ_SERIES（目前={XQ_SERIES!r}）。\n"
            f"可執行 python xqfap_feed.py --discover 找出正確的系列碼。"
        )
        sys.exit(1)

    # 推送初始化
    _post_init(contracts)

    # 初始快照
    _push_snapshot(_meta)

    # 啟動自動重新初始化排程
    threading.Thread(target=_auto_reinit_scheduler, daemon=True).start()

    # 啟動主輪詢迴圈（阻塞）
    logger.info(f"開始輪詢 {len(_meta)} 個合約（系列：TX4{XQ_SERIES}）...")
    _poll_loop()


if __name__ == '__main__':
    main()
