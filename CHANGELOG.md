# Changelog

## v1.4 (2026-03-23)

### 前端
- `static/style.css`：買盤欄改為紅色、賣盤欄改為綠色（符合台灣市場漲跌色彩慣例）

---

## v1.3 (2026-03-23)

### 新增欄位（T 字報價表）
- CALL / PUT 各新增「均價」欄（`avg_premium`，無成交時 fallback 前日收盤價），藍色顯示
- CALL / PUT 各新增「買盤量」（nTBc，外盤，綠色）與「賣盤量」（nTAc，內盤，紅色）欄
- 欄位順序：bar｜淨CALL｜均價｜買盤｜賣盤｜總量｜內外盤%｜履約價｜內外盤%｜總量｜賣盤｜買盤｜均價｜淨PUT｜bar

### 後端
- `calculator.py`：`build_strike_table` 新增 `avg_price_call`、`avg_price_put`、`ask_match_call`、`bid_match_call`、`ask_match_put`、`bid_match_put` 六個欄位

### 前端
- `static/index.html`：新增 6 個表頭欄位
- `static/app.js`：渲染新欄位，閃爍偵測涵蓋所有新欄
- `static/style.css`：新增欄位樣式，left-panel 寬度 580→860px

---

## v1.2 (2026-03-23)

### 新增腳本
- `start.bat`：Windows 一鍵啟動 WSL FastAPI server + Windows bridge
- `stop.bat`：Windows 一鍵停止兩邊所有 Python process
- `start_server.sh`：WSL 單獨啟動 FastAPI server（前台顯示 log）

### 文件
- 全面改寫 `HANDOFF.md`：包含架構圖、啟動流程、每週換倉說明、debug 指令、SKCOM 行為說明、已踩過的坑

## v1.1 (2026-03-23)

### 架構變更
- 移除富邦 SDK 依賴，改用群益 SKCOM API（`skcom_bridge.py`）
- Windows 端橋接：SKCOM DLL (ctypes) → HTTP POST → WSL FastAPI → WebSocket → Browser
- 新增 `skcom_bridge.py`：Windows 原生 Python，透過 ctypes 直接呼叫 SKCOM.dll

### 報價更新機制
- 訂閱 `OnNotifyQuoteLONG` callback，有 bid/ask 變動時即時推送
- 每 0.5 秒強制 `SKQuoteLib_RequestStocks` re-subscribe，確保夜盤成交量即時同步
  - SKCOM 夜盤不為每筆成交觸發 callback，re-subscribe 強制 SKCOM 推送全量最新快照
- 移除 per-symbol 輪詢（`GetStockByStockNo` poll），避免舊 cache 蓋掉新值

### Bug Fixes
- 修正 PUT 合約 callback 被 `market_no != 3` 過濾導致永不更新的問題
- 修正 `_poll_worker` 每 5 秒輪詢 DLL cache 造成數值來回跳動的問題
- 新增 zero-value 保護：夜盤 callback 送來 0 值時不覆蓋日盤已累計的量

### 前端
- 將 flash 動畫改為 per-cell 偵測（`prevValues` diff），只有實際變動的欄位才閃爍
- 新增 HTTP polling fallback（WS 超過 2 秒無資料自動改用 `/api/data`）
- 新增 WebSocket 斷線自動重連

### 後端 (main.py)
- `api_feed`：只有值真正改變才計入 `value_changed`，避免重複推送
- `websocket_endpoint`：新增 `finally` 確保斷線時可靠清理 clients set
- `_periodic_broadcast`：每 30 tick 印一次 heartbeat log

### 刪除
- `fetch_option_quote.py`、`fubon_client.py`、`phase1_test.py`（富邦 SDK 相關，已棄用）

## v1.0

- 初始版本，使用富邦 API 取得選擇權報價
- FastAPI WebSocket 廣播，ECharts 損益曲線
- T 字報價表（CALL/PUT 淨部位、總量、內外盤比）
