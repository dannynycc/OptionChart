# OptionBridge 開發計畫

最後更新：2026-03-24

---

## 現狀總結

### 架構（v1.5）
全部在 Windows 本機，不需要 WSL：

```
C:\Users\Home\Desktop\OptionBridge\
├── main.py              ← FastAPI server（uvicorn on Windows :8000）
├── calculator.py        ← 純計算邏輯
├── static/              ← 前端（index.html, app.js, style.css）
├── capital_bridge.py    ← 群益橋接（SKCOM.dll → POST /api/feed）
├── config_capital.py    ← 群益帳密（不進 git）
├── libs/SKCOM.dll       ← 群益 DLL
├── start.bat / stop.bat ← 一鍵啟動/停止
└── PLAN.md              ← 本檔案
```

### 資料流
```
群益SKCOM.dll
    ↓ OnNotifyQuoteLONG callback + 每0.5s re-subscribe
capital_bridge.py
    ↓ POST /api/init（啟動時一次）
    ↓ POST /api/feed（每0.5s批次）
main.py (uvicorn :8000)
    ↓ WebSocket broadcast
瀏覽器 http://localhost:8000
```

### Git
- Repo：https://github.com/dannynycc/OptionChart
- Windows OptionBridge/ 已直接 init git，不再依賴 WSL
- config_capital.py / config_fubon.py 不進 git（含帳密）

---

## Phase 1：收尾清理（待執行）

- [ ] 刪除 `config_bridge.py`（舊版群益設定，已被 config_capital.py 取代）
- [ ] 刪除 `C:\Users\Home\Desktop\wheels\`（安裝 fastapi/uvicorn 用的暫存 wheel，已無用）

---

## Phase 2：富邦橋接（主要工作）

### 目標
新增 `fubon_bridge.py` + `config_fubon.py`，讓系統可以從富邦期貨 API 接收 TXO 選擇權行情。
推送格式與群益完全相同，`main.py` / `calculator.py` / `static/` 完全不動。

### 富邦 API 基本資訊
- SDK：`fubon_neo`（Python package，`pip install fubon_neo`）
- 文件：https://www.fbs.com.tw/TradeAPI/docs/trading/introduction
- 登入需要：身分證字號、密碼、憑證路徑（.pfx）、憑證密碼

### config_fubon.py 格式
```python
ID            = "your_fubon_id"
PASSWORD      = "your_fubon_password"
CERT_PATH     = r"C:\path\to\cert.pfx"
CERT_PASSWORD = "your_cert_password"
SERVER_URL    = "http://localhost:8000"
TARGET_SERIES = "TXO"
```

### fubon_bridge.py 實作順序

#### Step 1：安裝 SDK
```
pip install fubon_neo
```
若無網路，用 WSL 下載 wheel 再離線安裝（同之前安裝 fastapi 的方式）。

#### Step 2：登入 + 初始化行情連線
```python
from fubon_neo.sdk import FubonSDK
sdk = FubonSDK()
accounts = sdk.login(cfg.ID, cfg.PASSWORD, cfg.CERT_PATH, cfg.CERT_PASSWORD)
sdk.init_realtime()
```

#### Step 3：取得近月 TXO 合約清單
- 端點：`GET /intraday/products/?type=OPTION&exchange=TAIFEX`
- 篩選：只取 TXO 格式、排除 AM 結算版、近月 = 最早 `settlementDate`
- 回傳欄位：`symbol`, `name`, `settlementDate`, `referencePrice`（作為 prev_close）

#### Step 4：POST /api/init
```json
{
  "settlement_date": "20260325",
  "contracts": [
    {"symbol": "TXOW40330C", "strike": 20330, "side": "C", "prev_close": 45.0},
    ...
  ]
}
```

#### Step 5：即時行情訂閱（WebSocket trades channel）
```python
stock.subscribe({'channel': 'trades', 'symbol': contract_symbol})

def on_message(message):
    # message 含逐筆 price, qty
    # avg_price 用最新成交價
    update_q.put({...})
```
注意：aggregates channel 是股票專用，選擇權一律用 `trades` channel。

#### Step 6：bid_match / ask_match（REST 定期輪詢）
trades channel 不直接給內/外盤累計量，需定期補充：
- 每 0.5s 呼叫：`GET /intraday/volumes/{symbol}`
- `volumeAtBid` → `bid_match`（內盤，賣方主動）
- `volumeAtAsk` → `ask_match`（外盤，買方主動）
- `volume`      → `trade_volume`（全日累計量）

#### Step 7：POST /api/feed（批次推送，同群益格式）
```json
[
  {
    "symbol": "TXOW40330C",
    "bid_match": 123,
    "ask_match": 456,
    "trade_volume": 579,
    "avg_price": 45.5
  }
]
```

#### Step 8：主迴圈
富邦 SDK 內建事件驅動，不需要 Windows MSG loop：
```python
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
```

---

## Phase 3：start.bat 支援切換 broker

```bat
@echo off
chcp 65001 >nul
set BROKER=%1
if "%BROKER%"=="" set BROKER=capital

echo [1/2] 啟動 FastAPI server...
start "OptionChart Server" /min cmd /c "python -m uvicorn main:app --host 0.0.0.0 --port 8000 >> bridge_out.log 2>> bridge_err.log"
timeout /t 2 /nobreak >nul

echo [2/2] 啟動 %BROKER% bridge...
python %BROKER%_bridge.py
pause
```

使用方式：
```
start.bat            ← 預設跑群益
start.bat capital    ← 明確指定群益
start.bat fubon      ← 跑富邦
```

---

## Phase 4：bridge_core.py 抽共用邏輯（選做）

等兩個 bridge 都跑通後評估，可抽出：
- `update_q`（共用推送 queue）
- `_http_worker()`（批次 POST /api/feed）
- `meta_map` 型別定義

---

## 關鍵欄位對照表

| 意義 | 群益（SKCOM） | 富邦（fubon_neo） |
|------|-------------|-----------------|
| 內盤累計量（賣方主動） | `nTAc` → `bid_match` | `volumeAtBid` → `bid_match` |
| 外盤累計量（買方主動） | `nTBc` → `ask_match` | `volumeAtAsk` → `ask_match` |
| 全日累計總量 | `nTQty` → `trade_volume` | `volume` → `trade_volume` |
| 最新成交價 | `nClose / 10^nDecimal` | trades callback `price` |
| 前日收盤價 | `nRef / 10^nDecimal` | `referencePrice`（products API）|
| 合約清單來源 | `SKQuoteLib_RequestStockList(3)` | `GET /intraday/products/?type=OPTION` |
| 即時行情來源 | `OnNotifyQuoteLONG` callback | WebSocket `trades` channel |

---

## 公式（不因 broker 而變）

```
外盤比(%) = ask_match / (bid_match + ask_match) × 100
淨部位    = bid_match - ask_match
CALL 損益 = Σ[ max(settlement - strike, 0) - avg_premium ] × net_position × 50
PUT  損益 = Σ[ max(strike - settlement, 0) - avg_premium ] × net_position × 50
Max Pain  = 合併損益最小值對應的履約價
```
