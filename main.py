"""
main.py
FastAPI 後端：接收 bridge 透過 HTTP 推送的 TXO 報價，廣播給瀏覽器（WebSocket）。
v2.17：多系列 stores，支援同時追蹤 N 個系列（每個有 full/day 兩份資料）。
"""

import json
import time
import logging
import asyncio
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from calculator import OptionData, calc_combined_pnl, build_strike_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
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

clients: set[WebSocket] = set()

# ── 計算 payload ──────────────────────────────────────────────

def compute_payload() -> dict:
    active_key = _active_full if _session_mode == 'full' else _active_day
    active = stores.get(active_key, {})
    with _lock:
        calls = [v for v in active.values() if v.side == 'C']
        puts  = [v for v in active.values() if v.side == 'P']
    pnl_result       = calc_combined_pnl(calls, puts)
    table            = build_strike_table(calls, puts)
    last_updated     = _last_updated.get(active_key, 0.0)
    subscribed_count = _subscribed_counts.get(active_key, 0)
    settlement       = _settlement_dates.get(active_key, "")
    return {
        "table":      table,
        "pnl":        pnl_result,
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
            old_ratio = opt.inout_ratio
            old_vol   = opt.trade_volume
            new_vol   = u.trade_volume if u.trade_volume > 0 else old_vol
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

    if value_changed:
        _last_updated[series] = time.time()
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

# ── 合約系列切換端點 ──────────────────────────────────────────

class SeriesPayload(BaseModel):
    series_full: str   # e.g. "TX4N03"
    series_day:  str   # e.g. "TX403"

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
    if clients:
        await broadcast(compute_payload())
    return {"ok": True}

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
    # 在每筆合約加上 live 旗標（是否已在 stores 中）
    result = []
    for c in _contracts_cache:
        c2 = dict(c)
        c2['live'] = c['series'] in stores
        result.append(c2)
    return {"contracts": result, "active_full": _active_full, "active_day": _active_day}

# ── 一般 HTTP 端點 ────────────────────────────────────────────

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

# ── WebSocket 端點 ────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    logger.info(f"瀏覽器連線，目前 {len(clients)} 個用戶")
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
