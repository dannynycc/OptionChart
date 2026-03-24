# OptionBridge 開發計畫

最後更新：2026-03-24

---

## 現狀總結

### 架構（v2.1）
全部在 Windows 本機，不需要 WSL：

```
C:\Users\Home\Desktop\OptionBridge\
├── main.py                     ← FastAPI server（uvicorn on Windows :8000）
├── calculator.py               ← 純計算邏輯（含日盤/日+夜雙模式欄位）
├── static/                     ← 前端（index.html, app.js, style.css）
├── capital_feed.py             ← 群益橋接（SKCOM.dll → POST /api/feed）
├── fubon_feed.py               ← 富邦橋接（fubon_neo SDK → POST /api/feed）
├── config_capital.py           ← 群益帳密（不進 git）
├── config_fubon.py             ← 富邦帳密（不進 git）
├── config_capital_template.py  ← 群益設定範本（進 git）
├── config_fubon_template.py    ← 富邦設定範本（進 git）
├── libs/SKCOM.dll              ← 群益 DLL
├── start.bat                   ← 一鍵後台啟動（PowerShell Hidden 模式）
├── stop.bat                    ← 停止
└── PLAN.md                     ← 本檔案
```

### 資料流
```
【群益】
群益SKCOM.dll
    ↓ OnNotifyQuoteLONG callback
capital_feed.py
    ↓ POST /api/init（啟動時一次）
    ↓ POST /api/feed（每0.5s批次）
main.py (uvicorn :8000)
    ↓ WebSocket broadcast
瀏覽器 http://localhost:8000

【富邦】
REST quote(session='afterhours') → _baseline（啟動時平行拉取一次）
    +
fubon_neo SDK（WebSocket futopt aggregates channel, Normal mode）
    ↓ 日盤值 + 夜盤基準 → queue
fubon_feed.py
    ↓ POST /api/init（啟動時一次）
    ↓ POST /api/feed（每0.5s批次，含 *_day 純日盤欄位）
main.py (uvicorn :8000)
    ↓ WebSocket broadcast
瀏覽器（日+夜 / 純日盤 切換按鈕）
```

### Git
- Repo：https://github.com/dannynycc/OptionChart
- config_capital.py / config_fubon.py 不進 git（含帳密）

### start.bat 使用方式
```
start.bat            ← 預設跑群益（背景無視窗）
start.bat capital    ← 明確指定群益
start.bat fubon      ← 跑富邦
```

---

## Phase 1：收尾清理 ✅ 完成

- [x] 刪除 legacy 橋接檔案（capital_bridge.py、config_bridge_template.py 等）
- [x] start.bat 改 PowerShell `-WindowStyle Hidden` 背景執行
- [x] 刪除 start_server.sh（WSL 時代產物）
- [x] 刪除 HANDOFF.md（由 PLAN.md 取代）

---

## Phase 2：富邦橋接 ✅ 完成（含夜盤基準）

### SDK 安裝
fubon_neo 不在 PyPI，從官網下載 zip 解壓後安裝 whl：
- 下載：`https://www.fbs.com.tw/TradeAPI_SDK/fubon_binary/fubon_neo-2.2.8-cp37-abi3-win_amd64.zip`
- 安裝：`pip install fubon_neo-2.2.8-cp37-abi3-win_amd64.whl`
- 已安裝：v2.2.8 ✅

### 行情取得策略

| 欄位 | 來源 |
|------|------|
| 內盤累計（日盤） | WS `total.totalBidMatch` → `bid_match_day` |
| 外盤累計（日盤） | WS `total.totalAskMatch` → `ask_match_day` |
| 成交量（日盤） | WS `total.tradeVolume` → `trade_volume_day` |
| 夜盤基準（各欄） | REST `quote(symbol, session='afterhours').total.*` |
| 日+夜合計 | 日盤值 + 夜盤基準 → `bid_match / ask_match / trade_volume` |
| 最新成交價 | WS `closePrice` 或 `lastPrice` |

- 啟動時 `ThreadPoolExecutor(max_workers=10)` 平行拉取所有合約夜盤基準
- `session='afterhours'`（非 'EXTENDED'）

### config_fubon.py 格式
```python
ID            = "your_fubon_id"
PASSWORD      = "your_fubon_password"
CERT_PATH     = r"C:\path\to\cert.pfx"
CERT_PASSWORD = "your_cert_password"
SERVER_URL    = "http://localhost:8000"
```

---

## Phase 3：start.bat 支援切換 broker ✅ 完成

```bat
start.bat            ← 預設跑群益
start.bat capital    ← 明確指定群益
start.bat fubon      ← 跑富邦
```

---

## Phase 4：bridge_core.py 抽共用邏輯（選做）

等兩個 feed 都穩定後評估，可抽出：
- `update_q`（共用推送 queue）
- `_http_worker()`（批次 POST /api/feed）

---

## Phase 5：日盤 / 日+夜 切換 ✅ 完成

前端加入 toggle 按鈕，在日+夜模式（預設）和純日盤模式之間切換。

### 資料管線
- `fubon_feed.py` 額外送 `bid_match_day` / `ask_match_day` / `trade_volume_day`（純日盤）
- `FeedItem`（main.py）對應欄位，預設 -1（群益未提供時的哨兵值）
- `OptionData`（calculator.py）加三個 `_day` 欄位
- `build_strike_table` 回傳 `*_day` 欄位；群益橋接回退用日+夜合計值（-1 哨兵）
- 前端 `app.js`：`showDayOnly` 旗標控制 suffix；快取 `window._lastRows` 供切換重繪

---

## 關鍵欄位對照表

| 意義 | 群益（SKCOM） | 富邦（fubon_neo WS） |
|------|-------------|-----------------|
| 內盤累計量（賣方主動） | `nTAc` → `bid_match` | `total.totalBidMatch` → `bid_match` |
| 外盤累計量（買方主動） | `nTBc` → `ask_match` | `total.totalAskMatch` → `ask_match` |
| 全日累計總量 | `nTQty` → `trade_volume` | `total.tradeVolume` → `trade_volume` |
| 最新成交價 | `nClose / 10^nDecimal` | `closePrice` 或 `lastPrice` |
| 前日收盤價 | `nRef / 10^nDecimal` | `referencePrice`（tickers API）|
| 合約清單來源 | `SKQuoteLib_RequestStockList(3)` | `intraday.tickers(type='OPTION')` |
| 即時行情來源 | `OnNotifyQuoteLONG` callback | WebSocket `futopt aggregates` channel |

---

## 公式（不因 broker 而變）

```
外盤比(%) = ask_match / (bid_match + ask_match) × 100
淨部位    = bid_match - ask_match
CALL 損益 = Σ[ max(settlement - strike, 0) - avg_premium ] × net_position × 50
PUT  損益 = Σ[ max(strike - settlement, 0) - avg_premium ] × net_position × 50
Max Pain  = 合併損益最小值對應的履約價
```
