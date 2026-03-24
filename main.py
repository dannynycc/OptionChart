"""
main.py
FastAPI 後端：接收 bridge（capital_bridge.py / fubon_bridge.py）透過 HTTP 推送的 TXO 報價，
廣播給瀏覽器（WebSocket）。與 bridge 同在 Windows 本機執行。
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

store: dict[str, OptionData] = {}
_lock = threading.Lock()

_settlement_date: str  = ""
_subscribed_count: int = 0
_last_updated: float   = 0.0
_connected: bool       = False

# 已連線的瀏覽器 WebSocket
clients: set[WebSocket] = set()

# ── 計算 payload ──────────────────────────────────────────────

def compute_payload() -> dict:
    with _lock:
        calls = [v for v in store.values() if v.side == 'C']
        puts  = [v for v in store.values() if v.side == 'P']
    pnl_result = calc_combined_pnl(calls, puts)
    table      = build_strike_table(calls, puts)
    return {
        "table":      table,
        "pnl":        pnl_result,
        "settlement": _settlement_date,
        "status": {
            "connected":        _connected,
            "subscribed_count": _subscribed_count,
            "settlement_date":  _settlement_date,
            "last_updated":     _last_updated,
            "error":            None,
        },
        "ts": time.time(),
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
    """每 1 秒廣播最新資料給所有已連線的瀏覽器（兼做 WS 心跳）"""
    tick = 0
    while True:
        await asyncio.sleep(1)
        tick += 1
        if tick % 30 == 0:
            logger.info(f"periodic_broadcast heartbeat: clients={len(clients)}, store={len(store)}")
        if clients and store:   # 有客戶端且有資料才廣播
            try:
                await broadcast(compute_payload())
            except Exception as e:
                logger.warning(f"periodic_broadcast error: {e}")

# ── App 生命週期 ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_periodic_broadcast())
    logger.info("OptionChart server 啟動，等待 capital_feed.py / fubon_feed.py 推送資料...")
    yield
    task.cancel()
    logger.info("關閉中...")

app = FastAPI(lifespan=lifespan)

# ── Bridge 端點：/api/init ────────────────────────────────────

class ContractMeta(BaseModel):
    symbol:     str
    strike:     int
    side:       str        # 'C' or 'P'
    prev_close: float = 0.0

class InitPayload(BaseModel):
    settlement_date: str
    contracts: list[ContractMeta]

@app.post("/api/init")
async def api_init(payload: InitPayload):
    """Windows bridge 啟動時推送合約清單"""
    global _settlement_date, _subscribed_count, _connected
    with _lock:
        store.clear()
        for c in payload.contracts:
            store[c.symbol] = OptionData(
                symbol     = c.symbol,
                strike     = c.strike,
                side       = c.side,
                prev_close = c.prev_close,
            )
    _settlement_date  = payload.settlement_date
    _subscribed_count = len(payload.contracts)
    _connected        = True
    logger.info(
        f"Bridge init: {len(payload.contracts)} 個合約，"
        f"結算日 {payload.settlement_date}"
    )
    return {"ok": True, "count": len(payload.contracts)}

# ── Bridge 端點：/api/feed ────────────────────────────────────

class FeedItem(BaseModel):
    symbol:           str
    bid_match:        int    # 內盤累計口數（日+夜合計）
    ask_match:        int    # 外盤累計口數（日+夜合計）
    trade_volume:     int
    avg_price:        float = 0.0
    bid_match_day:    int = -1   # 純日盤；-1 = 未提供（群益橋接不送此欄）
    ask_match_day:    int = -1
    trade_volume_day: int = -1

@app.post("/api/feed")
async def api_feed(updates: list[FeedItem]):
    """Windows bridge 批次推送報價更新；自動廣播給瀏覽器"""
    global _last_updated
    found = 0
    value_changed = 0
    with _lock:
        for u in updates:
            if u.symbol not in store:
                continue
            found += 1
            opt = store[u.symbol]
            # 只有值真正改變才算 value_changed，避免 SKCOM 送重複資料誤觸 last_updated
            # 同時防止夜盤 zero-value callback 蓋掉日盤累計值
            old_bid, old_ask, old_vol = opt.bid_match, opt.ask_match, opt.trade_volume
            new_bid = u.bid_match  if u.bid_match  > 0 else opt.bid_match
            new_ask = u.ask_match  if u.ask_match  > 0 else opt.ask_match
            new_vol = u.trade_volume if u.trade_volume > 0 else opt.trade_volume
            if new_bid != old_bid or new_ask != old_ask or new_vol != old_vol:
                opt.bid_match    = new_bid
                opt.ask_match    = new_ask
                opt.trade_volume = new_vol
                value_changed += 1
            # 純日盤欄位（富邦橋接才有，-1 = 未提供）
            if u.bid_match_day >= 0:
                opt.bid_match_day    = u.bid_match_day
                opt.ask_match_day    = u.ask_match_day
                opt.trade_volume_day = u.trade_volume_day
            if u.avg_price > 0:
                opt.avg_price = u.avg_price
    logger.info(
        f"api_feed: 收到 {len(updates)} 筆，found={found}，值變動={value_changed}，WS clients={len(clients)}"
    )
    if value_changed:
        _last_updated = time.time()
        await broadcast(compute_payload())
    return {"ok": True, "updated": value_changed}

# ── 一般 HTTP 端點 ────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("static/index.html")

@app.get("/api/data")
async def get_data():
    return compute_payload()

@app.get("/api/status")
async def get_status():
    return {
        "connected":        _connected,
        "subscribed_count": _subscribed_count,
        "settlement_date":  _settlement_date,
        "last_updated":     _last_updated,
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
        # 捕捉所有例外（含 WebSocketDisconnect、RuntimeError 等），確保清理
        pass
    finally:
        clients.discard(ws)
        logger.info(f"瀏覽器斷線，目前 {len(clients)} 個用戶")

# ── 靜態檔案 ──────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")
