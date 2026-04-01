"""
main.py
FastAPI 後端：接收 bridge 透過 HTTP 推送的 TXO 報價，廣播給瀏覽器（WebSocket）。
v2.17：多系列 stores，支援同時追蹤 N 個系列（每個有 full/day 兩份資料）。
"""

import json
import os
import sys
import time
import datetime
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
from core.taifex_calendar import PREFIX_RULES, settlement_date as calc_settlement_date, tf_name_label

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
_series_ready:     set   = set()  # 已完成第一輪 bulk_req 的 series（ready 才顯示給前端）
_contracts_cache:  list  = []   # 前端下拉選單資料
_connected:        bool  = False
_session_mode:     str   = "full"
_futures_price:    float = 0.0    # FITX*1 即時現價（由 xqfap_feed 定期推送）

clients: set[WebSocket] = set()
_ws_connect_count: int = 0   # 累計 WS 連線次數（刷新頁面會 +1）

# ── 快照管理 ──────────────────────────────────────────────────
_SNAPSHOT_DIR         = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'snapshots')
_snapshot_taken_today: dict[str, str] = {}  # series → date_str（已存快照的日期，防重複）

def _is_day_series(series: str) -> bool:
    return 'N' not in series

def _snap_prefix(series: str, settlement_date_str: str) -> str:
    """
    回傳快照檔名前綴，格式：{YY}_{label}
    例：TX1N04 + 2026-04-01 → '26_04W1'
        TXUN04 + 2026-04-07 → '26_04F1'
        TXON04 + 2026-04-15 → '26_04'
    """
    if not settlement_date_str:
        return ""
    sd = datetime.date.fromisoformat(settlement_date_str)
    yy = str(sd.year)[-2:]
    # 取 full series 的 prefix（日盤先補回 N 找 prefix）
    full = series if 'N' in series else series[:-2] + 'N' + series[-2:]
    n_idx = full.index('N')
    prefix = full[:n_idx]   # e.g. 'TX1', 'TXU', 'TXO'
    label = tf_name_label(prefix, sd.month)   # e.g. '04W1', '04F1', '04'
    return f"{yy}_{label}"

def _snap_filename(series: str, date_str: str, snap_type: str) -> str:
    """
    回傳完整快照檔名。
    snap_type: 'daily' → '{prefix}_{series}_{date}.json'
               'weekly_sum' → '{prefix}_{series}_{date}_weekly_sum.json'
    """
    sd_str = _settlement_dates.get(series, "")
    prefix = _snap_prefix(series, sd_str)
    if snap_type == 'weekly_sum':
        return f"{prefix}_{series}_{date_str}_weekly_sum.json"
    return f"{prefix}_{series}_{date_str}.json"

def _parse_snap_filename(fname: str) -> "dict | None":
    """
    解析快照檔名，回傳 {prefix, series, date, snap_type} 或 None。
    支援新格式：{YY}_{label}_{series}_{date}[_weekly_sum].json
    """
    if not fname.endswith('.json'):
        return None
    stem = fname[:-5]
    if stem.endswith('_weekly_sum'):
        snap_type = 'weekly_sum'
        stem = stem[:-len('_weekly_sum')]
    else:
        snap_type = 'daily'
    # 格式：{YY}_{label}_{series}_{YYYY-MM-DD}
    # 日期固定為 YYYY-MM-DD（10字元），前面是 _ 分隔符
    if len(stem) < 11 or stem[-11] != '_':
        return None
    date_str = stem[-10:]
    rest     = stem[:-11]   # 去掉 _{YYYY-MM-DD}
    # rest = {YY}_{label}_{series}，series 中可能含 N
    # series 部分：TXxN|TXx + 2位數字，前綴為 {YY}_{label}
    # 從右側找最長的合法 series（TX 開頭）
    idx = rest.rfind('_')
    if idx < 0:
        return None
    series   = rest[idx+1:]
    prefix   = rest[:idx]
    return {"prefix": prefix, "series": series, "date": date_str, "snap_type": snap_type}

def _series_last_updated(series: str) -> float:
    """回傳 series 的資料時間戳。
    - 已結算合約：固定回傳結算日 13:45:00
    - 日盤盤外（非 08:45~13:45）：固定回傳今天（或昨天）13:45:00
    """
    sd  = _settlement_dates.get(series, "")
    now = datetime.datetime.now()
    t   = now.time()
    if sd and sd <= now.date().isoformat() and t >= datetime.time(13, 45):
        return datetime.datetime.fromisoformat(f"{sd} 13:45:00").timestamp()
    if _is_day_series(series) and not (datetime.time(8, 45) <= t <= datetime.time(13, 45)):
        # 盤後（>13:45）→ 今天 13:45:00；盤前（<08:45）→ 昨天 13:45:00
        if t >= datetime.time(13, 45):
            ref_date = now.date()
        else:
            ref_date = now.date() - datetime.timedelta(days=1)
        return datetime.datetime.fromisoformat(f"{ref_date} 13:45:00").timestamp()
    return _last_updated.get(series, 0.0)

# ── 快照邏輯 ──────────────────────────────────────────────────

def _prev_contract_settlement(current_settlement_str: str) -> datetime.date:
    """
    找出讓位給當前合約的前一張合約結算日。
    掃描當前結算日前後 3 個月內所有前綴的結算日，
    取最大的且嚴格小於 current_settlement 的那個。
    """
    current = datetime.date.fromisoformat(current_settlement_str)
    candidates = []
    base = current.year * 12 + current.month - 1   # 0-indexed total months
    for offset in range(-3, 4):
        total = base + offset
        year  = total // 12
        month = total % 12 + 1
        if year < 2020:
            continue
        for prefix, _, _ in PREFIX_RULES:
            sd = calc_settlement_date(prefix, year, month)
            if sd and sd < current:
                candidates.append(sd)
    return max(candidates) if candidates else current - datetime.timedelta(days=7)


def _union_pnl(snapshots: list[dict]) -> dict:
    """多張快照的 strikes union 後逐點相加，缺失補 0。（舊算法，保留供 fallback）"""
    if not snapshots:
        return {"strikes": [], "pnl": []}
    all_strikes = sorted(set(s for snap in snapshots for s in snap["strikes"]))
    pnl = []
    for strike in all_strikes:
        val = 0.0
        for snap in snapshots:
            if strike in snap["strikes"]:
                val += snap["pnl"][snap["strikes"].index(strike)]
        pnl.append(round(val, 4))
    return {"strikes": all_strikes, "pnl": pnl}


def _virtual_twin_pnl(snapshots: list[dict], live_strikes: list[int]) -> dict:
    """
    虛擬孿生 baseline 算法：以今天 live_strikes 為全域 settlement 軸，
    對每個歷史快照的原始部位（raw_calls/raw_puts）重新計算全市場 pnl，再相加。

    核心差異：
      舊法：settlement=K 若該快照無此 strike → 填 0 → 邊界懸崖
      新法：settlement=K，用快照裡所有真實存在的 strike 計算 intrinsic，
            加總後仍為非零且連續 → 平滑曲線

    Backward compat：若快照缺少 raw_calls/raw_puts（舊格式），
    fallback 用預先計算的 pnl 字典查表補零。
    """
    if not live_strikes:
        return {"strikes": [], "pnl": []}

    total: dict[int, float] = {s: 0.0 for s in live_strikes}

    for snap in snapshots:
        raw_calls = snap.get("raw_calls")
        raw_puts  = snap.get("raw_puts")

        if raw_calls is not None and raw_puts is not None:
            # 新格式：虛擬孿生重算
            for settlement in live_strikes:
                c_sum = sum(
                    (max(settlement - c["strike"], 0) - c["avg_price"]) * c["net_pos"]
                    for c in raw_calls
                )
                p_sum = sum(
                    (max(p["strike"] - settlement, 0) - p["avg_price"]) * p["net_pos"]
                    for p in raw_puts
                )
                total[settlement] += (c_sum + p_sum) * 50 / 100_000_000
        else:
            # 舊格式 fallback：直接查表，缺失補 0
            pnl_map = dict(zip(snap.get("strikes", []), snap.get("pnl", [])))
            for settlement in live_strikes:
                total[settlement] += pnl_map.get(settlement, 0.0)

    return {
        "strikes": live_strikes,
        "pnl":     [round(total[s], 4) for s in live_strikes],
    }


def _try_save_snapshot(series: str) -> bool:
    """
    若 last_updated[series] >= 今天 13:45 且今天尚未存過，則存快照。
    全日盤（含 N）和日盤各自獨立觸發。回傳 True 表示本次有存檔。
    只對 active series（_active_full / _active_day）存檔，其餘略過。
    """
    if series not in (_active_full, _active_day):
        return False
    ts = _last_updated.get(series, 0.0)
    if not ts:
        return False
    dt   = datetime.datetime.fromtimestamp(ts)
    now  = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")

    # 條件：last_updated 是今天 且 >= 13:45:20（多留 20 秒確保收盤資料已完整推入）
    if dt.strftime("%Y-%m-%d") != today:
        return False
    if (dt.hour, dt.minute, dt.second) < (13, 45, 20):
        return False
    # 今天已存過
    if _snapshot_taken_today.get(series) == today:
        return False

    store = stores.get(series, {})
    with _lock:
        calls = [v for v in store.values() if v.side == 'C']
        puts  = [v for v in store.values() if v.side == 'P']
    result = calc_combined_pnl(calls, puts)
    if not result["strikes"]:
        return False
    snap_center = _futures_price if 'N' in series else 0
    atm, synthetic_map, implied_forward = calc_atm(calls, puts,
        center_price=snap_center, settlement_date=_settlement_dates.get(series, ""))
    table = build_strike_table(calls, puts, current_index=atm, synthetic_map=synthetic_map)

    os.makedirs(_SNAPSHOT_DIR, exist_ok=True)
    fname = _snap_filename(series, today, 'daily')
    path  = os.path.join(_SNAPSHOT_DIR, fname)
    raw_calls = [
        {"strike": c.strike, "net_pos": c.net_position, "avg_price": c.avg_premium}
        for c in calls if c.net_position != 0 or c.avg_premium > 0
    ]
    raw_puts = [
        {"strike": p.strike, "net_pos": p.net_position, "avg_price": p.avg_premium}
        for p in puts if p.net_position != 0 or p.avg_premium > 0
    ]
    snapshot = {
        "series":  series,
        "date":    today,
        "time":    "1345",
        "strikes":         result["strikes"],
        "pnl":             result["pnl"],
        "table":           table,
        "atm_strike":      atm,
        "implied_forward": implied_forward,
        "raw_calls":       raw_calls,
        "raw_puts":        raw_puts,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False)
    _snapshot_taken_today[series] = today
    logger.info(f"[snapshot] 已存 {fname}，raw_calls={len(raw_calls)}, raw_puts={len(raw_puts)}")

    # 全日盤系列額外存「當週全日盤累積」快照
    if 'N' in series:
        _try_save_weekly_snapshot(series, today, result["strikes"], result["pnl"])

    return True


def _try_save_weekly_snapshot(
    series: str, today: str,
    today_strikes: list, today_pnl: list,
):
    """
    在 1345 快照存完後，立刻計算當週全日盤累積（baseline＋今天）並存檔。
    today_strikes/today_pnl/today_raw_calls/today_raw_puts 直接從記憶體傳入，不重讀磁碟。
    baseline = 前幾天快照的 virtual twin pnl（不含今天）
    週累積 = baseline + today_pnl（直接逐點相加，共用 today_strikes 為 settlement 軸）
    """
    settlement_date = _settlement_dates.get(series, "")
    try:
        prev_settle = _prev_contract_settlement(settlement_date)
        week_start  = prev_settle + datetime.timedelta(days=1)
    except Exception:
        week_start = datetime.date.fromisoformat(today) - datetime.timedelta(days=datetime.date.fromisoformat(today).weekday())
    week_str = week_start.isoformat()

    # 只載前幾天（不含今天）的快照作為 baseline
    prev_snapshots = []
    for fname in sorted(os.listdir(_SNAPSHOT_DIR)):
        parsed = _parse_snap_filename(fname)
        if not parsed or parsed['snap_type'] != 'daily' or parsed['series'] != series:
            continue
        date = parsed['date']
        if date < week_str or date >= today:   # 嚴格小於今天
            continue
        with open(os.path.join(_SNAPSHOT_DIR, fname), 'r', encoding='utf-8') as f:
            prev_snapshots.append(json.load(f))

    # baseline：前幾天在 today_strikes 軸上重算
    baseline = _virtual_twin_pnl(prev_snapshots, today_strikes)
    # 週累積 = baseline + 今天 live pnl（逐點相加）
    weekly_pnl = [round(baseline["pnl"][i] + today_pnl[i], 4) for i in range(len(today_strikes))]

    sources = [f"{s['series']}_{s['date']}" for s in prev_snapshots] + [f"{series}_{today}"]
    weekly_path = os.path.join(_SNAPSHOT_DIR, _snap_filename(series, today, 'weekly_sum'))
    with open(weekly_path, 'w', encoding='utf-8') as f:
        json.dump({
            "series":     series,
            "date":       today,
            "time":       "weekly",
            "strikes":    today_strikes,
            "pnl":        weekly_pnl,
            "week_start": week_str,
            "sources":    sources,
        }, f, ensure_ascii=False)
    logger.info(f"[weekly-snapshot] 已存 {series}_{today}_weekly.json，來源：{sources}")


# ── 計算 payload ──────────────────────────────────────────────

def compute_payload() -> dict:
    active_key = _active_full if _session_mode == 'full' else _active_day
    active = stores.get(active_key, {})
    with _lock:
        calls = [v for v in active.values() if v.side == 'C']
        puts  = [v for v in active.values() if v.side == 'P']
    settlement       = _settlement_dates.get(active_key, "")
    pnl_result       = calc_combined_pnl(calls, puts)
    # 全日盤用 FITX 即時價作為 calc_atm center；日盤資料在 13:45 凍結，
    # 用夜盤即時價會讓合成期貨窗口偏移，改由資料本身推算隱含遠期（two-step fallback）
    center_price     = _futures_price if _session_mode == 'full' else 0
    atm_strike, synthetic_map, implied_forward = calc_atm(calls, puts, center_price=center_price, settlement_date=settlement)
    table            = build_strike_table(calls, puts, current_index=atm_strike, synthetic_map=synthetic_map)
    last_updated     = _series_last_updated(active_key)
    subscribed_count = _subscribed_counts.get(active_key, 0)
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
        "series": _active_full,
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
        if tick % 10 == 0:
            # 每 10 秒檢查一次快照觸發（防止 13:45 後資料靜止導致 api_feed 漏觸發）
            for series in list(stores.keys()):
                _try_save_snapshot(series)
        if clients and stores:
            try:
                await broadcast(compute_payload())
            except Exception as e:
                logger.warning(f"periodic_broadcast error: {e}")

# ── App 生命週期 ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 確保 snapshots 目錄存在，並掃描今天已有的快照防止重啟後重複存檔
    os.makedirs(_SNAPSHOT_DIR, exist_ok=True)
    today_str = datetime.date.today().isoformat()
    for fname in os.listdir(_SNAPSHOT_DIR):
        parsed = _parse_snap_filename(fname)
        if parsed and parsed['date'] == today_str and parsed['snap_type'] == 'daily':
            _snapshot_taken_today[parsed['series']] = today_str
    if _snapshot_taken_today:
        logger.info(f"[snapshot] 今日已存快照：{_snapshot_taken_today}")

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
    bid_price:        float = -1.0
    ask_price:        float = -1.0
    last_price:       float = -1.0

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
            if u.bid_price >= 0 and opt.bid_price != u.bid_price:
                opt.bid_price = u.bid_price
                value_changed += 1
            if u.ask_price >= 0 and opt.ask_price != u.ask_price:
                opt.ask_price = u.ask_price
                value_changed += 1
            if u.last_price >= 0 and opt.last_price != u.last_price:
                opt.last_price = u.last_price
                value_changed += 1

    if found > 0:
        _sd  = _settlement_dates.get(series, "")
        _now = datetime.datetime.now()
        _t   = _now.time()
        # 已結算合約：不更新時間戳
        _settled = bool(_sd and _sd <= _now.date().isoformat() and _t >= datetime.time(13, 45))
        # 日盤盤外（非 08:45~13:45）：資料不會有意義變動，不更新時間戳
        _day_offhours = _is_day_series(series) and not (datetime.time(8, 45) <= _t <= datetime.time(13, 45))
        if not _settled and not _day_offhours:
            _last_updated[series] = time.time()
        _try_save_snapshot(series)            # 若已到 13:45 且今天尚未存過，觸發快照
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
        sd  = _settlement_dates.get(series, "")
        now = datetime.datetime.now()
        t   = now.time()
        # 已結算合約：不更新
        if sd and sd <= now.date().isoformat() and t >= datetime.time(13, 45):
            return {"ok": True}
        # 日盤盤外：不更新
        if _is_day_series(series) and not (datetime.time(8, 45) <= t <= datetime.time(13, 45)):
            return {"ok": True}
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
    data = compute_payload()
    if clients:
        await broadcast(data)
    return {"ok": True, "payload": data}

# ── Series Ready 端點 ────────────────────────────────────────────

@app.post("/api/series-ready")
async def api_series_ready(series: str):
    """xqfap_feed 完成第一輪 bulk_req 後呼叫，標記 series 為 ready"""
    _series_ready.add(series)
    logger.info(f"series ready: {series}")
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
            _series_ready.discard(s)
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
        c2['live']         = fs in _series_ready or ds in _series_ready
        c2['total_count']  = _subscribed_counts.get(fs, 0)
        c2['loaded_count'] = sum(
            1 for o in stores.get(fs, {}).values()
            if o.bid_price > 0 or o.avg_price > 0
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
        "last_updated":     _series_last_updated(active_key),
        "error":            None,
    }

@app.get("/api/snapshots")
async def api_snapshots(series: str = "", settlement_date: str = ""):
    """回傳 snapshots/ 資料夾中屬於指定 series 的所有快照 metadata，按日期排序。
    全日盤系列加上 week_start 過濾，只顯示合約 active 期間的快照。"""
    # 計算 week_start（同 api_weekly_pnl 邏輯）
    week_str = ""
    if settlement_date:
        try:
            prev_settle = _prev_contract_settlement(settlement_date)
            week_str    = (prev_settle + datetime.timedelta(days=1)).isoformat()
        except Exception:
            pass

    result = []
    try:
        for fname in sorted(os.listdir(_SNAPSHOT_DIR)):
            parsed = _parse_snap_filename(fname)
            if not parsed:
                continue
            s    = parsed['series']
            date = parsed['date']
            snap_type = parsed['snap_type']
            if series and s != series:
                continue
            if week_str and date < week_str:
                continue
            session = "全日盤" if 'N' in s else "日盤"
            if snap_type == 'weekly_sum':
                label = f"{date} 當週全日盤累積"
                t = "weekly"
            else:
                t = "1345"
                label = f"{date} 13:45 ({session})"
            result.append({"filename": fname, "series": s, "date": date, "time": t, "label": label})
    except Exception as e:
        logger.warning(f"api_snapshots error: {e}")
    return {"snapshots": result}


@app.get("/api/snapshots/{filename}")
async def api_snapshot_file(filename: str):
    """回傳單張快照的完整資料（strikes + pnl）。"""
    # 防止 path traversal
    if '/' in filename or '\\' in filename or '..' in filename:
        return {"error": "invalid filename"}
    path = os.path.join(_SNAPSHOT_DIR, filename)
    if not os.path.exists(path):
        return {"error": "not found"}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


@app.get("/api/weekly-pnl")
async def api_weekly_pnl(series: str = "", settlement_date: str = ""):
    """
    回傳當前合約 active 期間內的快照加總，供前端 weekly 模式 baseline。
    week_start = 前一張合約結算日 + 1 天（那天之後本合約才成為 default）。
    今天是否納入取決於 14:35 XQFAP 重整：
      - 14:35 前：live pnl 仍代表今天 → 排除今天快照
      - 14:35 後：live pnl 已重整 → 納入今天快照
    若無 settlement_date，fallback 為本週一。
    已結算合約（settlement_date <= 今天 且時間 >= 13:45）：直接回傳 weekly 快照，不重算。
    """
    now       = datetime.datetime.now()
    # 已結算合約：直接讀 weekly 快照，避免因 live_strikes 漂移導致曲線不一致
    if series and settlement_date:
        _sd_now = now.date().isoformat()
        if settlement_date <= _sd_now and now.time() >= datetime.time(13, 45):
            _weekly_file = os.path.join(_SNAPSHOT_DIR, _snap_filename(series, settlement_date, 'weekly_sum'))
            if os.path.exists(_weekly_file):
                with open(_weekly_file, 'r', encoding='utf-8') as _f:
                    _data = json.load(_f)
                    _data['_settled'] = True   # 前端 _mergeWithLive 用此旗標，不再疊加 live_pnl
                    return _data
    today     = now.date()
    today_str = today.isoformat()

    if settlement_date:
        try:
            prev_settle = _prev_contract_settlement(settlement_date)
            week_start  = prev_settle + datetime.timedelta(days=1)
        except Exception:
            week_start = today - datetime.timedelta(days=today.weekday())
    else:
        week_start = today - datetime.timedelta(days=today.weekday())
    week_str = week_start.isoformat()
    # 14:35 後 XQFAP 重整，live pnl 已清空，今天快照必須納入；14:35 前 live 仍代表今天，排除今天快照
    session_reset = (now.hour > 14) or (now.hour == 14 and now.minute >= 35)

    snapshots = []
    try:
        for fname in sorted(os.listdir(_SNAPSHOT_DIR)):
            parsed = _parse_snap_filename(fname)
            if not parsed or parsed['snap_type'] != 'daily':
                continue
            if series and parsed['series'] != series:
                continue
            date = parsed['date']
            if date < week_str:
                continue
            if date > today_str:
                continue
            if date == today_str and not session_reset:
                continue
            with open(os.path.join(_SNAPSHOT_DIR, fname), 'r', encoding='utf-8') as f:
                snapshots.append(json.load(f))
    except Exception as e:
        logger.warning(f"api_weekly_pnl error: {e}")

    # 取今天 live strike 列表作為全域 settlement 軸（以傳入 series 的全日盤為準）
    live_series = series if series and series in stores else _active_full
    live_store  = stores.get(live_series, {})
    with _lock:
        live_options = list(live_store.values())
    live_strikes = sorted(set(o.strike for o in live_options))

    result = _virtual_twin_pnl(snapshots, live_strikes)
    result["week_start"] = week_str
    result["sources"] = [f"{s['series']}_{s['date']}" for s in snapshots]
    return result


@app.post("/api/force-snapshot")
async def api_force_snapshot(series: str = ""):
    """強制用目前記憶體資料重建快照，忽略時間限制和今天已存過的限制。"""
    if not series or series not in stores:
        return {"ok": False, "error": f"series {series!r} not found in stores"}
    store = stores.get(series, {})
    with _lock:
        calls = [v for v in store.values() if v.side == 'C']
        puts  = [v for v in store.values() if v.side == 'P']
    result = calc_combined_pnl(calls, puts)
    if not result["strikes"]:
        return {"ok": False, "error": "store 有資料但 pnl 計算為空"}
    # 日盤快照用 two-step 推算（不用夜盤即時期貨價，會偏移）
    snap_center = _futures_price if 'N' in series else 0
    atm, synthetic_map, implied_forward = calc_atm(calls, puts,
        center_price=snap_center, settlement_date=_settlement_dates.get(series, ""))
    table = build_strike_table(calls, puts, current_index=atm, synthetic_map=synthetic_map)
    today = datetime.date.today().isoformat()
    os.makedirs(_SNAPSHOT_DIR, exist_ok=True)
    fname = _snap_filename(series, today, 'daily')
    path  = os.path.join(_SNAPSHOT_DIR, fname)
    raw_calls = [
        {"strike": c.strike, "net_pos": c.net_position, "avg_price": c.avg_premium}
        for c in calls if c.net_position != 0 or c.avg_premium > 0
    ]
    raw_puts = [
        {"strike": p.strike, "net_pos": p.net_position, "avg_price": p.avg_premium}
        for p in puts if p.net_position != 0 or p.avg_premium > 0
    ]
    snapshot = {
        "series":          series,
        "date":            today,
        "time":            "1345",
        "strikes":         result["strikes"],
        "pnl":             result["pnl"],
        "table":           table,
        "atm_strike":      atm,
        "implied_forward": implied_forward,
        "raw_calls":       raw_calls,
        "raw_puts":        raw_puts,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False)
    _snapshot_taken_today[series] = today
    logger.info(f"[force-snapshot] 強制重建 {fname}，{len(result['strikes'])} 個履約價，raw_calls={len(raw_calls)}, raw_puts={len(raw_puts)}")
    return {"ok": True, "filename": fname, "strikes_count": len(result["strikes"])}


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
