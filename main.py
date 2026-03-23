"""
main.py
FastAPI 後端主程式。
"""

import json
import time
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import config
from fubon_client import FubonClient
from calculator import calc_combined_pnl, build_strike_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── 全域狀態 ──────────────────────────────────────────────
fubon   = FubonClient(config.ID, config.PASSWORD, config.CERT_PATH, config.CERT_PASS)
clients: set[WebSocket] = set()          # 已連線的瀏覽器 WebSocket

# ── 廣播邏輯 ──────────────────────────────────────────────

def compute_payload() -> dict:
    """計算最新結果，組成 JSON payload"""
    calls, puts = fubon.get_store_snapshot()
    pnl_result  = calc_combined_pnl(calls, puts)
    table       = build_strike_table(calls, puts)   # 之後可傳入目前指數
    return {
        "table":      table,
        "pnl":        pnl_result,
        "settlement": fubon.settlement_date,
        "status":     fubon.get_status(),
        "ts":         time.time(),
    }

async def broadcast(payload: dict):
    """廣播給所有已連線的瀏覽器"""
    if not clients:
        return
    msg = json.dumps(payload, ensure_ascii=False, default=str)
    dead = set()
    for ws in clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)

# fubon_client 的 on_update callback（在 thread 內執行）
def on_fubon_update():
    payload = compute_payload()
    # 跨 thread 呼叫 asyncio，需用 run_coroutine_threadsafe
    try:
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(broadcast(payload), loop)
    except RuntimeError:
        pass   # loop 尚未啟動（啟動前的更新忽略）

# ── App 生命週期 ───────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("啟動 FubonClient...")
    fubon.on_update = on_fubon_update

    import threading
    t = threading.Thread(target=fubon.start, daemon=True)
    t.start()

    yield   # 應用程式執行中

    logger.info("關閉中...")

app = FastAPI(lifespan=lifespan)

# ── HTTP 端點 ─────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse("static/index.html")

@app.get("/api/data")
async def get_data():
    """初始載入用：回傳目前最新計算結果"""
    return compute_payload()

@app.get("/api/status")
async def get_status():
    return fubon.get_status()

# ── WebSocket 端點 ────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    logger.info(f"瀏覽器連線，目前 {len(clients)} 個用戶")

    # 連線後立即推一次目前資料
    try:
        payload = compute_payload()
        await ws.send_text(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        pass

    try:
        while True:
            await ws.receive_text()   # 保持連線，不處理前端訊息
    except WebSocketDisconnect:
        clients.discard(ws)
        logger.info(f"瀏覽器斷線，目前 {len(clients)} 個用戶")

# ── 靜態檔案 ─────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")
