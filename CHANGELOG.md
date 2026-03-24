# Changelog

## v2.5 (2026-03-24)

### 新增：新富邦e01 DDE 橋接（xqfap_feed.py）

- **新報價源**：直接從新富邦e01 DDE（XQFAP server）取得選擇權外盤/內盤**口數**
  - `OutSize` → `bid_match`（外盤口數，完全對應 XQ 顯示）
  - `InSize`  → `ask_match`（內盤口數）
  - `TotalVolume`, `AvgPrice` 同步取得
- **取代群益**：`capital_feed.py` 停用，改由 `xqfap_feed.py` 驅動
- **啟動方式**：`start.bat xqfap`（現有 bat 已支援 %BROKER% 參數）
- **合約探索**：啟動時自動查 `FITX00.TF-Price` 取得指數中心，探索 ±3500 範圍內所有有效合約
- **換週更新**：只需改 `config_xqfap.py` 的 `XQ_SERIES`（當週合約系列碼）+ `SETTLEMENT_DATE`
- **--discover 模式**：`python xqfap_feed.py --discover` 自動找出本月所有可用系列碼
- **自動重新初始化**：08:43 / 14:58 盤前自動重探合約（與 capital_feed.py 行為一致）
- 新增 `config_xqfap_template.py`（範本），`config_xqfap.py` 不進 git

### 技術背景

- XQ 資料來源確認：新富邦e01（daqFAP.exe）是 DDE server，名稱 XQFAP，topic Quote
- symbol 格式：`TX4{SERIES}C{strike}` / `TX4{SERIES}P{strike}`（e.g., TX4N03C32600）
- 不需要開 Excel；只要新富邦e01 開著即可
- 資料與 XQ 軟體畫面的 OutSize/InSize 一致

---

## v2.4 (2026-03-24)

### 損益圖表全面重設計

- **雙色面積**：X 軸以上紅色、以下綠色（兩個隱形 area series，各自 origin:0 填色）
- **空心藍圓圈**：每個履約價一個資料點標記
- **Hover 插值**：滑鼠在任意 X 位置做線性插值，顯示藍色虛線十字準星 + 實心藍點 + tooltip（正值紅/負值綠）
- **X 軸範圍改為 noUiSlider**：取代 ECharts 內建 slider，拖拉順滑；rAF 節流確保 60fps 回應
- **Y 軸動態縮放**：隨 X 範圍變動自動計算可見資料極值（含邊界插值），上下留 12% 空間
- **單位改為萬元**（原億元）

### T 字報價表改進

- **損益驗證欄**：最右側新增 Call損益(億)、Put損益(億)、合併損益(億) 三欄，對應圖表 Y 值
- **docstring 修正**：`_calc_call_pnl` / `_calc_put_pnl` 改為「全市場淨損益」，反映實際語意
- **左側面板可拖拉調整寬度**：中間分隔線拖拉，縮小後 T 字表出現水平捲動條

### 版面調整

- **狀態列移入工具列**：移除固定定位，改為工具列右側，不再遮住圖表底部
- **欄位標題簡化**：「總成交量」→「成交量」、「成交均價」→「均價」

---

## v2.3 (2026-03-24)

### 修正：Call 內外盤% 公式錯誤（stale __pycache__ 導致舊 bytecode 持續生效）

- `calculator.py`：`ratio_call` 分子由 `ask_match` 改為 `bid_match`（外盤/Buy Call 比例）
  - 舊：`ask_match / total`（算內盤比，顯示 33.9% 但應為 66.1%）
  - 新：`bid_match / total`（外盤比，與 XQFAP 及欄位定義一致）
- 根本原因：Windows uvicorn process（PID 2756）未被 bash `kill` 殺到，持續載入舊 `.pyc`
  - 修復方式：`netstat -ano` 查 Windows PID → `powershell Stop-Process -Force` 殺掉

### 新增：盤前自動重新初始化排程（capital_feed.py）

- 新增 `_auto_reinit_scheduler()` 背景執行緒，每 20 秒檢查一次
- 於 08:43（日盤前）、14:58（夜盤前）自動呼叫 `_load_and_subscribe()`
- 效果：跨盤時清空 server store、重新訂閱合約，避免舊盤資料污染新盤

### UI 調整：T 字報價表欄位重排與視覺優化

- **欄位順序重排**
  - Call 側（左→右）：外盤成交量(Buy Call) | 內盤成交量(Sell Call) | 成交量 | 內外盤% | 均價 | 淨Call | bar
  - Put 側（左→右）：bar | 淨Put | 外盤成交量(Buy Put) | 內盤成交量(Sell Put) | 成交量 | 內外盤% | 均價
- **欄位顏色**：外盤成交量(Buy Call/Put)=紅；內盤成交量(Sell Call/Put)=綠（Put 側相反）
- **Bar 改為中心軸**：pct 縮放至 0~50%，Call 正=紅朝左/負=綠朝右；Put 正=綠朝右/負=紅朝左
- **淨值動態顯色**：淨Call 正=紅/負=綠；淨Put 正=綠/負=紅
- **欄位標題簡化**：「總成交量」→「成交量」、「成交均價」→「均價」

---

## v2.2 (2026-03-24)

### 修正：內外盤欄位對調錯誤（三層 bug fix）

#### 問題根源
內盤（賣盤）與外盤（買盤）數值顯示錯誤，共三個獨立 bug 疊加：

1. **`calculator.py`：`inout_ratio` 公式反向**
   - 舊：`ask_match / total * 100`（算的是內盤比，與欄位名「外盤比」矛盾）
   - 新：`bid_match / total * 100`（外盤=買方主動，與 XQFAP 定義一致）

2. **`static/app.js`：買盤/賣盤欄位對調**
   - `bid_match`（外盤口數）誤放入 `col-call-sell`（賣盤欄）
   - `ask_match`（內盤口數）誤放入 `col-call-buy`（買盤欄）
   - CALL/PUT 兩側均修正

3. **`capital_feed.py`：nTBc / nTAc 對調**
   - 根據群益 SKCOM API Manual + Eskmo 文件確認：
     `nTBc` = 外盤量（買方主動，口數，全日累計）
     `nTAc` = 內盤量（賣方主動，口數，全日累計）
   - 舊：`bid_match = s.nTAc`（錯）、`ask_match = s.nTBc`（錯）
   - 新：`bid_match = s.nTBc`（外盤）、`ask_match = s.nTAc`（內盤）
   - 修正三處：初始快照、`_on_notify_quote_long`、`_on_notify_ticks_long`
   - 修正錯誤 comment：移除「nTAc+nTBc 只從訂閱後累計」（Eskmo 確認為全日累計）
   - Log 訊息加上 `(外盤)/(內盤)` 標示，方便現場驗證

### 新增：富邦 trades channel 精確口數累計（fubon_feed.py）

#### 問題
富邦 aggregates channel 的 `totalBidMatch`/`totalAskMatch` 為**筆數**（trade count），非口數（contract volume）。比例換算（筆數 ratio × 成交量）在大單/小單混合時失真。

#### 解法
- 新增訂閱 `trades` WebSocket channel（與 `aggregates` 共用同一 callback）
- 每筆成交依 `price vs bid/ask` 判斷方向，累計精確口數到 `_exact_vol`
- `aggregates` 更新時優先使用 `_exact_vol`；啟動前的歷史成交無法取得，接受不完整基準
- 新增 `_seen_serials` set，防止重連時 snapshot 重送造成重複累計

---

## v2.1 (2026-03-24)

### 夜盤基準值（afterhours baseline）— 修正日+夜合計成交量

#### 問題
富邦 aggregates WebSocket 只推送當前交易時段（日盤）的累計量，夜盤成交量不包含在內。
導致網頁顯示的成交量只有日盤數值（例如 127），而非日+夜合計（例如 909）。

#### 解法（fubon_feed.py）
- 啟動時用 `ThreadPoolExecutor(max_workers=10)` 平行拉取所有合約夜盤基準：
  `rc.quote(symbol=sym, session='afterhours').total`
- 每次 WS 更新：`combined = 日盤值 + _baseline[symbol]`
- 額外傳送純日盤欄位：`bid_match_day / ask_match_day / trade_volume_day`

### 日盤 / 日+夜 切換按鈕

- 工具列新增「日+夜」按鈕（預設），點擊切換為「日盤」
- `FeedItem`（main.py）新增 `bid_match_day`, `ask_match_day`, `trade_volume_day`（預設 -1 哨兵）
- `OptionData`（calculator.py）對應新增 3 個 `_day` 欄位；`build_strike_table` 回傳 `*_day`
- 群益橋接未提供 `_day` 時（-1），前後端自動回退用日+夜合計值
- `app.js`：`gC(row, field)` 依模式取 `field_day` 或 `field`；快取 `window._lastRows` 供切換重繪

### start.bat：背景無視窗啟動
- 改用 `PowerShell Start-Process -WindowStyle Hidden`
- server 與 feed 均以隱藏視窗後台執行，不會跳出 cmd 視窗

### 前端安全性（XSS 防護）
- `app.js updateTable` 改用 `document.createElement` + `textContent`，移除所有 innerHTML 模板字串拼接

### 檔案重命名
- `capital_bridge.py` → `capital_feed.py`
- `fubon_bridge.py` → `fubon_feed.py`

### 刪除 legacy 檔案
- `capital_bridge.py`, `config_bridge_template.py`, `list_exports.py`, `setup_libs.py`, `start_server.sh`, `HANDOFF.md`

### 新增
- `config_capital_template.py`：群益設定範本
- `config_fubon_template.py`：富邦設定範本

---

## v1.5 (2026-03-24)

### 架構重構：移除 WSL 依賴，全面搬移至 Windows 本機

#### 搬移
- `main.py`、`calculator.py`、`static/` 從 WSL `~/OptionChart/` 搬至 Windows `OptionBridge/`
- FastAPI server 改在 Windows 本機以 `uvicorn` 執行，不再需要 WSL

#### 檔案重命名
- `skcom_bridge.py` 改名為 `capital_bridge.py`（明確標示為群益橋接）
- `config_bridge.py` 改名為 `config_capital.py`（明確標示為群益設定）

#### 腳本更新
- `start.bat`：移除 `wsl` 指令，改為 Windows 本機啟動 uvicorn
- `stop.bat`：移除 WSL pkill，只 kill Windows python.exe

#### 相依套件
- Windows 端安裝 `fastapi`、`uvicorn`（離線 wheel 方式安裝）

---

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
