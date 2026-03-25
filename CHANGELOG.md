# Changelog

## v2.26 (2026-03-26)

### xqfap_feed.py 多執行緒 DDEML 輪詢池

**架構改變：`_poll_meta` 由 pywin32 單執行緒 → DDEML ThreadPoolExecutor**
- `_NUM_DDEML_WORKERS = 3`：3 個 worker thread，各自持有獨立 DDEML 連線（threading.local lazy init）
- `_poll_meta` 將 symbols 以 `[i::3]` 切分，平行提交 3 個 future，匯集結果
- 每個 worker 讀 TotalVolume + InOutRatio + AvgPrice 全走 DDEML（不再分 pywin32/DDEML）
- InOutRatio DDEML 回傳帶 `%` 後綴，`_req_thread` 統一 `rstrip('%')` 處理
- `_poll_meta_single` 保留為 fallback（executor 未就緒時使用 pywin32）

**可行性依據**：`--test-ddeml` 模式驗證 XQFAP 支援多條並行 DDEML 連線（3/4 workers ok=18 fail=0）

**預估效果**：活躍盤 ~4-5s/輪 → ~1.5-2s/輪（3x 加速）

**變更檔案**：
- **`xqfap_feed.py`**：`import concurrent.futures`；`_NUM_DDEML_WORKERS/threading.local/_poll_executor`；`_thread_ddeml_connect/_req_thread/_poll_meta_chunk/_init_poll_executor()`；`_poll_meta` 多執行緒版；`_poll_meta_single` fallback

---

## v2.25 (2026-03-26)

### xqfap_feed.py 移除分層輪詢，保留 vol-skip 優化

移除 v2.24 加入的分層輪詢（優化②），回歸全履約價均等輪詢。
保留優化①：TotalVolume 不變 → skip InOutRatio + AvgPrice。

**變更檔案**：`xqfap_feed.py`

---

## v2.24 (2026-03-26)

### xqfap_feed.py 雙重 DDE call 優化 — 靜盤 2x 加速、全範圍分層輪詢

**優化①：TotalVolume 不變 → 直接 skip InOutRatio + AvgPrice**
- 原本：每個 symbol 永遠讀 TotalVolume + InOutRatio（2 calls），值變才讀 AvgPrice
- 現在：TotalVolume 不變 → InOutRatio 數學上必然不變（無新成交則 OutSize 不變）→ `continue`
- 靜盤時：248 calls 從 2 calls/symbol → 1 call/symbol，節省 ~50% DDE 呼叫

**優化②：分層輪詢（按距中心距離）**
- 近價平（dist < 1500 點）：每輪全速更新
- 中間（dist 1500~3000 點）：每 2 輪更新
- 深 OTM/ITM（dist > 3000 點）：每 4 輪更新
- 每輪有效 symbols：~112（原 248 的 45%）→ 預估活躍盤 ~4-5s/輪
- 深 OTM 資料仍完整顯示（初始 snapshot 已推送），只是更新頻率降為每 ~40s

**中心價快取（_get_center_cached）**
- 每 30s 更新一次 FITX00 中心價，供分層輪詢計算距離用，不影響 DDE 主路徑

**其他**
- `_poll_ticks` module-level dict（per-series tick 計數，供分層輪詢使用）
- `_poll_meta` 新增 `series` 參數

**多執行緒評估（暫不實作）**：DDE thread affinity 限制 `_conv.Request()` 只能在建立 `_conv` 的 thread 上呼叫。理論上可建多個獨立 DDE 連線（每 thread 各自 CreateServer+ConnectTo），但 pywin32 global state 與 XQFAP 多連線支援未驗證，風險未知，留待後續測試。

**變更檔案**：
- **`xqfap_feed.py`**：`import re`；`_poll_ticks`/`_center_cache*/`_TIER*` 常數；`_get_center_cached()`；`_poll_meta` 分層+skip 邏輯

---

## v2.23 (2026-03-26)

### xqfap_feed.py 非 active 系列不再干擾主畫面更新頻率

**根本原因修正：`series_times` 初始為 0 導致非 active 立刻觸發**
- 原本 `series_times = {}` → `get(series, 0)` 回傳 0 → 每次 active 輪完（~11s）非 active 馬上判定「已超過間隔」→ 再花 ~11s → 用戶感受 ~22s
- 改為 module-level `_series_times`，_poll_loop 啟動時以 `time.time()` 初始化所有已知系列 timer
- `_load_one_series` 載入新系列後也以 `setdefault` 設定 timer，防止背景載入完成後立刻觸發

**`_SLOW_FULL = 60`（原 15）**
- 非 active 每 60s 才插入一次輪詢；主畫面穩態幾乎每 ~10s 更新，偶爾一次 ~21s（每分鐘一次非 active）

**變更檔案**：
- **`xqfap_feed.py`**：`_series_times` 模組級 dict；timer 初始化；`_SLOW_FULL=60`

---

## v2.22 (2026-03-26)

### xqfap_feed.py 啟動速度 + 主畫面更新頻率進一步優化

**poll_loop 節拍從 1s 縮短至 0.1s**
- `_reinit_flag.wait(timeout=1.0)` → `0.1s`
- Active 系列每輪最多等 0.1s，實際輪詢上限從 ~1s 提升至幾乎無延遲（受限 DDE 呼叫本身耗時）

**啟動時只立即探索第一個合約（active），其餘排入背景佇列**
- `fast_list = series_with_sd[:1]`，其餘 3 個放入 `_bg_load_queue`
- 第一個合約探索完即進入 `_poll_loop`，主畫面立刻開始快速更新
- 其他合約由 `_poll_loop` 背景每輪載入一個，不阻塞主畫面

**AvgPrice 延遲讀取（vol/ratio 不變時跳過 DDEML call）**
- 只有 `InOutRatio` 或 `TotalVolume` 有異動才呼叫 `_get_avg_price()`
- 靜盤時節省大量 DDEML 開銷

**非 active 系列間隔調整**
- `_SLOW_FULL = 15`（原 10）

**變更檔案**：
- **`xqfap_feed.py`**：`timeout=0.1`；`fast_list[:1]` + `_bg_load_queue`；AvgPrice 延遲讀取；`_SLOW_FULL=15`

---

## v2.21 (2026-03-26)

### xqfap_feed.py 輪詢效能優化

**Fix ①：移除 `_poll_meta` Name 查詢**
- 原本每個 symbol 輪詢時都先查 `TF-Name` 做 validity check（探索階段已確認存在，重複查詢）
- 移除後每個 symbol 從 4 次 DDE call 降為 3 次，節省 ~25% DDE 呼叫量

**輪詢速率統一**
- 原本 full/day 分開設定（`_FAST_FULL=1s/_FAST_DAY=3s`，`_SLOW_FULL=10s/_SLOW_DAY=30s`）
- 改為統一：active 系列 1s，非 active 系列 10s（full/day 一視同仁）

**時間基準改為 `time.time()`（time-based）**
- 原本用 tick 計數器（`series_ticks`），每 tick ≈ 1s（但 DDE 阻塞時 1 tick 實際可達 13s+）
- 改用 `time.time()` 真實秒數，interval 語義回歸「幾秒輪詢一次」
- 非 active 系列每輪至多輪詢一個（防止多個同時到期堆積，避免依序輪動）

**Active 系列偵測加速**
- 原本每 5 tick 才查一次 `/api/active-series`，切換合約最多延遲 5s 才升速
- 改為每 tick 查一次，切換後下一輪（≤1s）立刻升速

**夜盤優化：日盤系列停止輪詢**
- 新增 `_is_night_session()`（15:00~05:00）
- 夜盤期間日盤資料不變，跳過日盤系列輪詢，節省約一半 DDE 呼叫
- 啟動時初始快照仍完整推送；08:43 盤前 reinit 時自動刷新

**變更檔案**：
- **`xqfap_feed.py`**：移除 Name check；統一速率常數；time-based `series_times`；active 每 tick 查；新增 `_is_night_session()`；`_poll_loop` 重構（active 優先 + 非 active 每輪一個）

---

## v2.20 (2026-03-25)

### 合約篩選精簡 + 圖表 UX 修正 + 下拉選單提早出現 + UI 微調

**合約追蹤精簡（xqfap_feed.py）**：
- `_scan_valid_series` 從 12 個月（120 probe）縮短為當月+下個月（20 probe）
- `main()` 新增篩選：最近 3 個週選（TX 非 TXO）+ 最近 1 個月選（TXO），其餘不追蹤
- 移除 `_fast_series.add()`，改由 `is_active` 決定輪詢速率（active=1s/3s，非 active=10s/30s），避免 4 組全速輪詢消耗大量 DDE 呼叫

**下拉選單提早出現**：
- 原本 4 個合約全部探索完才推送 `_post_contracts`，導致下拉顯示「載入中...」很久
- 改為每探索完一個合約就推送一次，第一個 ready 即顯示（其餘帶 `•`）

**圖表 UX 修正（static/app.js）**：
- **Y 軸不自動 fit**：切換合約/盤別後 Y 軸不跟著資料縮放，需拖動 slider 才 fit。根本原因：`_recalcYAxis` 只在 `forceReset=true` 時才呼叫。修正為每次 `updateChart` 都以 slider 當前位置重算 Y 軸
- **滑鼠滾輪失效**：圖表無任何 wheel 事件處理。新增 wheel listener，調整 noUiSlider X 範圍（向上縮小 factor=0.87、向下放大 factor=1.15），Y 軸跟著 slider `update` 事件自動重算

**UI 微調（static/style.css）**：
- T 字表欄位 padding 從 4px 縮為 2px（欄間距更緊湊）
- 左側面板預設寬度從 1060px 改為 862px（豎線預設落在 Put均價 與 Call損益(億) 之間）

**變更檔案**：
- **`xqfap_feed.py`**：`_scan_valid_series` 縮至 2 個月；`main()` 3週+1月篩選；每合約完成即推送清單；移除 `_fast_series.add`
- **`static/app.js`**：`updateChart` 永遠重算 Y 軸；新增 wheel 縮放
- **`static/style.css`**：padding 2px；左側寬度 862px

---

## v2.19 (2026-03-25)

### 修正：· 未消失前點進去應顯示空白；active 系列強制每秒更新

**Bug 1：提早點進 `·` 系列，卻看到舊資料**
- 根本原因：`_post_init()` 一呼叫就讓 `c['series'] in stores` 為 True，`/api/contracts` 立刻回傳 `live=True`，但 snapshot 尚未推送完畢。app.js 5s poll 立刻移除 `·`，用戶進去看到大片空格。
- **Fix（main.py）**：`live` 改判 `_last_updated.get(fs, 0) > 0`，確保至少收到一次 feed 更新才算 ready。

**Bug 2：切換到 slow tier 合約，資料每 10s 才更新**
- 根本原因：背景載入的合約（slow tier）DDE 輪詢間隔為 10s，用戶切換後更新頻率沒跟著升。
- **Fix（main.py + xqfap_feed.py）**：新增 `GET /api/active-series` 端點；`_poll_loop` 每 5 tick 查一次，active 系列強制以 fast rate（全日盤 1s / 日盤 3s）輪詢。

**新功能：`·` 系列空白等待 + 自動切換**
- 點選 `·` 未 ready 系列 → `_clearDisplay()` 清空 table + chart，`_viewingNonLive = true`
- 停留畫面期間 WebSocket 資料不更新 table/chart（只維持連線燈）
- 5s poll 偵測到 `·` 消失 → 自動呼叫 `_switchSeries(c)`，資料一次全部刷入

**變更檔案**：
- **`main.py`**：`api_contracts_get` live 判斷改用 `_last_updated > 0`；新增 `GET /api/active-series`
- **`xqfap_feed.py`**：新增 `_fetch_active_series()`；`_poll_loop` active 系列強制 fast rate
- **`static/app.js`**：`_viewingNonLive` 旗標；`_clearDisplay()`；`handleData` 暫停更新；5s poll 偵測到 live 後自動切換

---

## v2.18 (2026-03-25)

### 新增：Progressive Loading — 前3系列 fast tier，其餘背景 slow tier 載入

**功能說明**：
- 啟動時掃描所有有效系列，前 3 個（結算日最近）立即探索並初始化（fast tier：全日盤每 1s、日盤每 3s）
- 其餘系列加入 `_bg_load_queue`，`_poll_loop` 每輪處理一個（slow tier：全日盤每 10s、日盤每 30s）；背景在同一 thread 執行保留 DDE thread affinity
- 背景系列完成探索+init+snapshot 後，呼叫 `_post_contracts()` 通知前端移除 `·`
- app.js 每 5s 輪詢 `/api/contracts`，偵測新 live 系列即更新下拉選單文字

**變更檔案**：
- **`xqfap_feed.py`**：新增 `_fast_series`、`_bg_load_queue`、`_all_valid_series` 全域；`_load_one_series()` helper；`_poll_loop` 改用 per-series tick counter；`main()` 前3 fast + 其餘 bg queue
- **`static/app.js`**：新增 5s interval 輪詢 `/api/contracts` 刷新 live 狀態

---

## v2.16 (2026-03-25)

### 新增：工具列合約下拉選單 + 期交所代號欄

**功能說明**：
- 網頁啟動時自動掃描 XQFAP 所有有效系列（`_scan_valid_series()`），結果依結算日排序後推送至 `/api/contracts`
- 前端工具列新增「合約選擇」下拉選單，預設選最近未到期合約
- 新增「期交所代號」欄位（位於「日盤(一般)」按鈕右側），隨合約選擇 + 日/夜盤切換即時更新
- 結算日格式改為 `2026-03-25(三)`，含星期，並正確反映假日順延

**變更檔案**：
- **`main.py`**：新增 `_contracts_cache`、`POST /api/contracts`、`GET /api/contracts` 端點
- **`xqfap_feed.py`**：新增 `_post_contracts()`；啟動後呼叫 `_scan_valid_series()` 掃全部有效系列並推送；結果依 `settlement_date` 排序
- **`taifex_calendar.py`**：新增 `tf_name_label(prefix, month)` 回傳 XQFAP TF-Name 標籤（`03W4` / `03F4` / `04`）；`fetch_holidays()` 修正 SSL 憑證驗證（TWSE 憑證缺 SKI）；`--discover` 輸出依結算日排序並顯示標籤
- **`static/index.html`**：工具列加「合約選擇」`<select>`、「期交所代號」`<span>`
- **`static/app.js`**：`fetchContracts()` + `_updateSeriesCode()`；`handleData` 修正 WS session_mode 同步按鈕視覺（修正期交所代號顯示全日盤卻顯示 TX403 的 bug）
- **`static/style.css`**：`#contract-select` 粗體藍色；移除 T字表底部水平 scrollbar（`#table-scroll overflow-x: hidden`）

---

## v2.15 (2026-03-25)

### 新增：taifex_calendar.py — TAIFEX 選擇權合約完整邏輯模組

將 TAIFEX 臺指選擇權的命名規則、結算日推導、有效合約掃描邏輯整理為獨立模組。

**`taifex_calendar.py`**（新增）：
- `PREFIX_RULES`：10 個前綴代碼完整定義（TX1/TX2/TXO/TX4/TX5 週三；TXU/TXV/TXX/TXY/TXZ 週五）
- `nth_weekday(year, month, n, weekday)`：計算當月第 N 個指定週幾
- `fetch_holidays(year)`：從 TWSE 取得年度休市日（LRU 快取，不重複 fetch）
- `next_trading_day(date, holidays)`：若當日為休市日則順延至最近交易日
- `settlement_date(prefix, year, month)`：計算含假日順延的實際結算日
- `series_full(prefix, month)` / `series_day(prefix, month)`：全日盤/日盤系列碼
- `day_from_full(full_series)`：全日盤 → 日盤（去掉 N）
- `build_scan_plan(center)`：產生 120 組 XQFAP 探索測試（當前2月10前綴+後10月TXO×4測試點）

**`CLAUDE.md`**（更新）：加入 TAIFEX 命名規則參考說明，指向 taifex_calendar.py。

---

## v2.8 (2026-03-25)

### 修正：均價（AvgPrice）小數精度遺失

**根本原因**：pywin32 `dde` 模組的 `Conversation.Request()` 在讀取 CF_TEXT 回應時，因緩衝區處理缺陷導致小數位被截斷（`'0.4'` 讀成 `'0.'`，`'93.3'` 讀成 `'93.'`）。實測：OTM 低價合約均價應為 0.3～0.9 點，但實際收到 0，造成損益曲線計算錯誤。

**修正**：`xqfap_feed.py` 改用 Windows DDEML API（`user32.dll` + ctypes），直接呼叫 `DdeClientTransaction` + `DdeGetData`，先查資料大小再分配緩衝，完整讀取字串。無需 pywin32 `dde` 模組。

- **`xqfap_feed.py`**：
  - 移除 `import win32ui` / `import dde`，改用 `ctypes.WinDLL("user32")`
  - 新增 DDEML 宣告（`_PFNCALLBACK`, `_dde_callback`, `_dde_inst`, `_dde_hconv`）
  - `_connect_dde()`：改用 `DdeInitializeW` + `DdeConnect`
  - `_request()`：改用 `DdeClientTransaction` + `DdeGetData`（正確保留小數位）
  - `_to_float()`：加 `.rstrip('%')` 處理 DDEML 回傳的百分比格式（`'44.84%'`）

---

## v2.7 (2026-03-25)

### 修正：內外盤比率與淨口數公式（對齊 Excel Golden）

**根本原因**：原先抓 `OutSize`（nTAc）和 `InSize`（nTBc），但 XQFAP 的 `InOutRatio = OutSize / TotalVolume × 100`，分母是 `TotalVolume`（含開盤競價），而 `OutSize + InSize` 不含開盤競價，兩個分母不同，導致淨口數和損益曲線偏高。

- **`xqfap_feed.py`**：改拿 `TF-InOutRatio`（移除 `TF-OutSize` / `TF-InSize`），feed item 改送 `inout_ratio` + `trade_volume` + `avg_price`
- **`calculator.py` `OptionData`**：
  - 新增 `inout_ratio: float = 50.0` 為主要欄位（直接來自 XQFAP）
  - `bid_match`（外盤/Buy）= `round(inout_ratio/100 × trade_volume)`（顯示用，由推導）
  - `ask_match`（內盤/Sell）= `trade_volume - bid_match`（顯示用，由推導）
  - `net_position` 改為 `round((inout_ratio - 50) / 50 × trade_volume)` ← 完全對應 Excel 公式
  - `ratio_call` / `ratio_put` 改直接用 `inout_ratio`（不再用 bid/(bid+ask)）
- **`main.py` `FeedItem`**：加 `inout_ratio` 欄位；`api_feed` 支援 xqfap 路徑（inout_ratio 為主）和 fubon 路徑（bid/ask 為主，反推 inout_ratio）向下相容

---

## v2.6 (2026-03-25)

### 雙 Store 即時切換（全日盤 ↔ 日盤）

- **核心架構升級**：`main.py` 同時維護 `store_full`（TX4N03）和 `store_day`（TX403）兩張獨立 store
- **切換瞬間完成**：按下切換按鈕 → POST `/api/set-session` → main.py 立即廣播另一 store 的資料，不再觸發 reinit
  - 切換延遲從原本 8~14 秒降至 **<100ms**
- **`xqfap_feed.py` 雙線輪詢**：
  - 移除 `_poll_session_mode` 背景 thread（舊架構的 busy-poll 反模式）
  - 啟動時從 full 合約自動推導 day 合約（`_build_day_meta`），不需重新 DDE 探索
  - full 系列每輪輪詢；day 系列每 3 輪輪詢一次（節省 DDE 資源，inactive 時略為落後可接受）
  - `_poll_loop` 改用 `_reinit_flag.wait(timeout=1.0)` 取代 `time.sleep`，旗標觸發立即喚醒
- **`/api/feed` 加 `?mode=` 參數**：更新指向正確的 store；inactive store 更新靜默儲存、不廣播
- **合約數各自追蹤**：`_subscribed_count_full` / `_subscribed_count_day`，顯示 active 那個
- **資料時間各自追蹤**：`_last_updated_full` / `_last_updated_day`，顯示 active 那個
- **圖表座標軸獨立**：切換模式時前端 `forceReset=true`，slider 重置到全範圍，Y 軸依新資料重算，不沿用舊 scale

---

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
