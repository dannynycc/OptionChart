"""
main.py
FastAPI 後端：接收 bridge 透過 HTTP 推送的 TXO 報價，廣播給瀏覽器（WebSocket）。
v2.17：多系列 stores，支援同時追蹤 N 個系列（每個有 full/day 兩份資料）。
"""

import json
import os
import sys
import time
import logging
import logging.handlers
import asyncio
import threading
import socket
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from core.calculator import OptionData, calc_combined_pnl, build_strike_table, calc_atm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'monitor')
os.makedirs(_log_dir, exist_ok=True)
_fh = logging.handlers.RotatingFileHandler(
    os.path.join(_log_dir, 'server.log'),
    maxBytes=10 * 1024 * 1024, backupCount=3, encoding='utf-8',
)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_fh)
logger = logging.getLogger(__name__)

# ── 全域資料 store ────────────────────────────────────────────
# stores 以 series 為 key，例如 "TX4N03" / "TX403" / "TXYN03" / "TXY03"
stores:           dict[str, dict[str, OptionData]] = {}
_lock = threading.Lock()

_active_full:      str   = ""   # 當前顯示的全日盤系列，例如 "TX4N03"
_active_day:       str   = ""   # 當前顯示的日盤系列，例如 "TX403"
_settlement_dates: dict  = {}   # series → settlement_date str
_subscribed_counts:dict  = {}   # series → 合約數
_last_updated:     dict  = {}   # series → float timestamp
_contracts_cache:  list  = []   # 前端下拉選單資料
_connected:        bool  = False
_session_mode:     str   = "full"
_futures_price:    float = 0.0    # FITX*1 即時現價（由 xqfap_feed 定期推送）

clients: set[WebSocket] = set()
_ws_connect_count: int = 0   # 累計 WS 連線次數（刷新頁面會 +1）

# ── 計算 payload ──────────────────────────────────────────────

def compute_payload() -> dict:
    active_key = _active_full if _session_mode == 'full' else _active_day
    active = stores.get(active_key, {})
    with _lock:
        calls = [v for v in active.values() if v.side == 'C']
        puts  = [v for v in active.values() if v.side == 'P']
    pnl_result       = calc_combined_pnl(calls, puts)
    atm_strike, synthetic_map, implied_forward = calc_atm(calls, puts, center_price=_futures_price)
    table            = build_strike_table(calls, puts, current_index=atm_strike, synthetic_map=synthetic_map)
    last_updated     = _last_updated.get(active_key, 0.0)
    subscribed_count = _subscribed_counts.get(active_key, 0)
    settlement       = _settlement_dates.get(active_key, "")
    return {
        "table":      table,
        "pnl":        pnl_result,
        "atm_strike":      atm_strike,
        "implied_forward": implied_forward,
        "settlement": settlement,
        "status": {
            "connected":        _connected,
            "subscribed_count": subscribed_count,
            "settlement_date":  settlement,
            "last_updated":     last_updated,
            "error":            None,
        },
        "ts": time.time(),
        "session_mode": _session_mode,
    }

# ── 廣播 ─────────────────────────────────────────────────────

async def broadcast(payload: dict):
    if not clients:
        return
    msg  = json.dumps(payload, ensure_ascii=False, default=str)
    dead = set()
    for ws in list(clients):
        try:
            await ws.send_text(msg)
        except Exception as e:
            logger.warning(f"broadcast send failed: {e}")
            dead.add(ws)
    if dead:
        clients.difference_update(dead)
        logger.info(f"broadcast: 移除 {len(dead)} 個死連線，剩 {len(clients)} 個")

# ── 定時廣播（心跳 + 保活） ──────────────────────────────────

async def _periodic_broadcast():
    """每 1 秒廣播最新資料給所有已連線的瀏覽器"""
    tick = 0
    while True:
        await asyncio.sleep(1)
        tick += 1
        if tick % 30 == 0:
            logger.info(f"heartbeat: clients={len(clients)}, stores={list(stores.keys())}")
        if clients and stores:
            try:
                await broadcast(compute_payload())
            except Exception as e:
                logger.warning(f"periodic_broadcast error: {e}")

# ── App 生命週期 ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_periodic_broadcast())
    logger.info("OptionChart server 啟動，等待 xqfap_feed.py 推送資料...")
    yield
    task.cancel()
    logger.info("關閉中...")

app = FastAPI(lifespan=lifespan)

# ── Bridge 端點：/api/init ────────────────────────────────────

class ContractMeta(BaseModel):
    symbol:     str
    strike:     int
    side:       str
    prev_close: float = 0.0

class InitPayload(BaseModel):
    settlement_date: str
    contracts: list[ContractMeta]
    series: str        # 全日盤如 "TX4N03"，日盤如 "TX403"
    merge:  bool = False

@app.post("/api/init")
async def api_init(payload: InitPayload):
    """bridge 啟動時推送合約清單，以 series 為 key 存入 stores"""
    global _active_full, _active_day, _connected
    series = payload.series
    is_full = 'N' in series   # TX4N03 有 N；TX403 沒有

    if series not in stores:
        stores[series] = {}
    target = stores[series]

    with _lock:
        if not payload.merge:
            target.clear()
        for c in payload.contracts:
            target[c.symbol] = OptionData(
                symbol     = c.symbol,
                strike     = c.strike,
                side       = c.side,
                prev_close = c.prev_close,
            )

    _settlement_dates[series]  = payload.settlement_date
    _subscribed_counts[series] = len(target)

    # 第一個 full/day 系列自動成為 active
    if is_full and not _active_full:
        _active_full = series
        _connected   = True
    if not is_full and not _active_day:
        _active_day = series

    logger.info(
        f"Bridge init [{series}] {'merge' if payload.merge else 'replace'}: "
        f"{len(payload.contracts)} 個合約（store 共 {len(target)} 個），結算日 {payload.settlement_date}"
    )
    return {"ok": True, "count": len(target)}

# ── Bridge 端點：/api/feed ────────────────────────────────────

class FeedItem(BaseModel):
    symbol:           str
    trade_volume:     int
    inout_ratio:      float = -1.0
    bid_match:        int   = -1
    ask_match:        int   = -1
    avg_price:        float = 0.0
    bid_match_day:    int   = -1
    ask_match_day:    int   = -1
    trade_volume_day: int   = -1
    bid_price:        float = 0.0
    ask_price:        float = 0.0
    last_price:       float = 0.0

@app.post("/api/feed")
async def api_feed(updates: list[FeedItem], series: str = ""):
    """bridge 批次推送報價更新；只有 active series 的更新才廣播"""
    if not series or series not in stores:
        return {"ok": False, "error": f"series {series!r} not found in stores"}

    target        = stores[series]
    found         = 0
    value_changed = 0

    with _lock:
        for u in updates:
            if u.symbol not in target:
                continue
            found += 1
            opt     = target[u.symbol]
            # trade_volume=0 代表 quote-only 更新（僅 bid/ask/last），不碰成交量欄位
            if u.trade_volume > 0:
                old_ratio = opt.inout_ratio
                old_vol   = opt.trade_volume
                new_vol   = u.trade_volume
                if u.inout_ratio >= 0:
                    new_ratio = u.inout_ratio
                    new_bid   = round(new_ratio / 100 * new_vol)
                    new_ask   = new_vol - new_bid
                else:
                    new_bid   = u.bid_match if u.bid_match >= 0 else opt.bid_match
                    new_ask   = u.ask_match if u.ask_match >= 0 else opt.ask_match
                    new_ratio = new_bid / new_vol * 100 if new_vol > 0 else 50.0
                if new_ratio != old_ratio or new_vol != old_vol:
                    opt.inout_ratio  = new_ratio
                    opt.trade_volume = new_vol
                    opt.bid_match    = new_bid
                    opt.ask_match    = new_ask
                    value_changed   += 1
            if u.bid_match_day >= 0:
                opt.bid_match_day    = u.bid_match_day
                opt.ask_match_day    = u.ask_match_day
                opt.trade_volume_day = u.trade_volume_day
            if u.avg_price > 0:
                opt.avg_price = u.avg_price
            if u.bid_price > 0 and opt.bid_price != u.bid_price:
                opt.bid_price = u.bid_price
                value_changed += 1
            if u.ask_price > 0 and opt.ask_price != u.ask_price:
                opt.ask_price = u.ask_price
                value_changed += 1
            if u.last_price > 0 and opt.last_price != u.last_price:
                opt.last_price = u.last_price
                value_changed += 1

    if found > 0:
        _last_updated[series] = time.time()   # 有收到合約資料即更新（含盤後無變動）
    if value_changed:
        # 只有當前 active series 更新才廣播（背景 series 靜默儲存）
        active_key = _active_full if _session_mode == 'full' else _active_day
        if series == active_key:
            await broadcast(compute_payload())

    return {"ok": True, "updated": value_changed}

# ── Session 模式端點 ──────────────────────────────────────────

class SessionModePayload(BaseModel):
    mode: str  # "full" | "day"

@app.post("/api/set-session")
async def api_set_session(payload: SessionModePayload):
    global _session_mode
    if payload.mode not in ("full", "day"):
        return {"ok": False, "error": "invalid mode"}
    _session_mode = payload.mode
    logger.info(f"session mode → {payload.mode}")
    if clients:
        await broadcast(compute_payload())
    return {"ok": True}

@app.get("/api/get-session")
async def api_get_session():
    return {"mode": _session_mode}

@app.post("/api/heartbeat")
async def api_heartbeat(series: str = ""):
    """bg_poll 心跳：只更新 last_updated 時間戳，不推送資料"""
    if series and series in stores:
        _last_updated[series] = time.time()
    return {"ok": True}

@app.post("/api/set-futures-price")
async def api_set_futures_price(payload: dict):
    global _futures_price
    price = float(payload.get("price", 0))
    if price > 0 and price != _futures_price:
        _futures_price = price
        await broadcast(compute_payload())
    return {"ok": True}

@app.get("/api/active-series")
async def api_active_series():
    """供 xqfap_feed.py 查詢目前 active 系列，以決定 DDE 輪詢頻率"""
    return {"full": _active_full, "day": _active_day}

# ── 合約系列切換端點 ──────────────────────────────────────────

class SeriesPayload(BaseModel):
    series_full: str   # e.g. "TX4N03"
    series_day:  str   # e.g. "TX403"

def _notify_feeder(series_full: str):
    """通知 xqfap_feed.py 立即切換系列（TCP socket，port 8001）。"""
    try:
        with socket.create_connection(('127.0.0.1', 8001), timeout=0.5) as s:
            s.sendall(series_full.encode('utf-8'))
    except Exception:
        pass  # feeder 未啟動或 socket 失敗，_series_watcher fallback 會補上


@app.post("/api/set-series")
async def api_set_series(payload: SeriesPayload):
    global _active_full, _active_day
    if payload.series_full not in stores:
        return {"ok": False, "error": f"{payload.series_full!r} 尚未載入"}
    if payload.series_day not in stores:
        return {"ok": False, "error": f"{payload.series_day!r} 尚未載入"}
    _active_full = payload.series_full
    _active_day  = payload.series_day
    logger.info(f"active series → full={_active_full}, day={_active_day}")
    threading.Thread(target=_notify_feeder, args=(payload.series_full,), daemon=True).start()
    if clients:
        await broadcast(compute_payload())
    return {"ok": True}

# ── 廢棄系列清理端點 ──────────────────────────────────────────

class PurgeSeriesPayload(BaseModel):
    keep: list[str]   # 目前仍有效的系列清單，不在此清單的系列將從 stores 移除

@app.post("/api/purge-series")
async def api_purge_series(payload: PurgeSeriesPayload):
    """xqfap_feed reinit 完成後呼叫，清除 stores 中已過期的舊系列"""
    keep_set = set(payload.keep)
    stale = [s for s in list(stores.keys()) if s not in keep_set]
    if not stale:
        return {"ok": True, "removed": []}
    with _lock:
        for s in stale:
            stores.pop(s, None)
            _settlement_dates.pop(s, None)
            _subscribed_counts.pop(s, None)
            _last_updated.pop(s, None)
    logger.info(f"purge-series：移除廢棄系列 {stale}，保留 {list(keep_set)}")
    return {"ok": True, "removed": stale}

# ── 合約下拉清單端點 ──────────────────────────────────────────

class ContractsPayload(BaseModel):
    contracts: list[dict]

@app.post("/api/contracts")
async def api_contracts_post(payload: ContractsPayload):
    global _contracts_cache
    _contracts_cache = payload.contracts
    logger.info(f"contracts cache 更新：{len(_contracts_cache)} 個系列")
    return {"ok": True, "count": len(_contracts_cache)}

@app.get("/api/contracts")
async def api_contracts_get():
    # live = 該系列已收到至少一次 feed（snapshot 完成才算 ready，避免空資料時就移除 ·）
    result = []
    for c in _contracts_cache:
        c2   = dict(c)
        fs   = c['series']                 # full series e.g. "TX1N04"
        ds   = fs.replace('N', '')         # day series  e.g. "TX104"
        c2['live'] = (
            _last_updated.get(fs, 0) > 0 or
            _last_updated.get(ds, 0) > 0
        )
        result.append(c2)
    return {"contracts": result, "active_full": _active_full, "active_day": _active_day}

# ── 一般 HTTP 端點 ────────────────────────────────────────────

@app.get("/api/debug")
async def api_debug():
    """記憶體/資源監控端點：回傳 stores 大小、clients 數、asyncio tasks 數"""
    import asyncio, threading
    stores_info = {s: len(v) for s, v in stores.items()}
    tasks = [t for t in asyncio.all_tasks() if not t.done()]
    return {
        "stores":           stores_info,
        "stores_total":     sum(stores_info.values()),
        "clients":          len(clients),
        "asyncio_tasks":    len(tasks),
        "threads":          threading.active_count(),
        "last_updated":     dict(_last_updated),
        "ws_connect_count": _ws_connect_count,
        "futures_price":    _futures_price,
    }

@app.get("/")
async def index():
    return FileResponse("static/index.html")

@app.get("/api/data")
async def get_data():
    return compute_payload()

@app.get("/api/status")
async def get_status():
    active_key = _active_full if _session_mode == 'full' else _active_day
    return {
        "connected":        _connected,
        "subscribed_count": _subscribed_counts.get(active_key, 0),
        "settlement_date":  _settlement_dates.get(active_key, ""),
        "last_updated":     _last_updated.get(active_key, 0.0),
        "error":            None,
    }

@app.post("/api/restart-feed")
async def restart_feed():
    """終止舊 xqfap_feed.py（依 xqfap.pid）並重新啟動。"""
    base = os.path.dirname(os.path.abspath(__file__))
    pid_file = os.path.join(base, 'monitor', 'xqfap.pid')
    # 終止舊 process
    try:
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        subprocess.run(['taskkill', '/F', '/PID', str(old_pid)],
                       capture_output=True, check=False)
        logger.info(f"restart-feed: 已終止 pid={old_pid}")
    except Exception as e:
        logger.warning(f"restart-feed: 終止舊 process 失敗（{e}），繼續啟動新的")
    # 啟動新 process，stdout/stderr 導向 logs/xqfap.log
    log_path = os.path.join(base, 'monitor', 'xqfap.log')
    log_file = open(log_path, 'a', encoding='utf-8')
    subprocess.Popen(
        [sys.executable, 'xqfap_feed.py'],
        cwd=base,
        stdout=log_file,
        stderr=log_file,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    logger.info("restart-feed: 已啟動新 xqfap_feed.py")
    return {"status": "restarting"}

# ── WebSocket 端點 ────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global _ws_connect_count
    await ws.accept()
    clients.add(ws)
    _ws_connect_count += 1
    logger.info(f"瀏覽器連線，目前 {len(clients)} 個用戶（累計第 {_ws_connect_count} 次）")
    try:
        await ws.send_text(json.dumps(compute_payload(), ensure_ascii=False, default=str))
    except Exception:
        pass
    try:
        while True:
            await ws.receive_text()
    except Exception:
        pass
    finally:
        clients.discard(ws)
        logger.info(f"瀏覽器斷線，目前 {len(clients)} 個用戶")

# ── 靜態檔案 ──────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")
