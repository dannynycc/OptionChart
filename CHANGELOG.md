# Changelog

## v5.3 (2026-04-08)

### 盤中快照補存 + 14:35 重整 + quote_poll 完整欄位 + UI 修正

#### 後端：資料完整性
- **盤中快照重啟補存**：server 重啟後 60 秒檢查最近一次 intraday 快照時間，若間隔 > 30 分鐘且在交易時段，立即補存（使用真實時間，不偽造）
- **14:35 stores 自動重整**：配合 XQFAP 每日 14:35 資料重整，清空所有 OptionData 的成交欄位（trade_volume/inout_ratio/avg_price/bid/ask/last），保留合約結構
- **quote_poll 讀完整 6 欄位**：從原本只讀 Bid/Ask/Price，改為同時讀 TotalVolume/InOutRatio/AvgPrice，不再依賴 advise callback 推送成交量（修復夜盤成交量顯示不出來的問題）

#### 後端：快照邏輯調整
- **每天都存 weekly_sum**：weekly_sum 不再限定結算日才存，每天 13:45 都存當週累積快照
- **week_start 改為結算日當天**：`prev_settle + 1` → `prev_settle`，結算日的資料歸入新週（04W1 結算日的 04F2 資料屬於 04F2 新週）
- **快照列表排序修正**：`/api/snapshots` 統一按 date + time 排序，13:45 收盤快照落在當天 intraday 最後

#### 前端 UI
- **快照 → 即時切換修復**：選 weekly_sum（無 table）後清空 table DOM 和 `_rowMap`，切回即時時主動 fetch `/api/data` 重建 table（不依賴 WS 推送）
- **Toolbar 一行排版**：toolbar-item 加 `white-space: nowrap`，status-bar 加 `flex-shrink: 0`，隱藏「已連線」假狀態和「複製表格」按鈕，gap/padding 縮小適應 150% DPI

## v5.2 (2026-04-08)

### 損益視圖左右分類 + 夜盤後半修正

#### 前端 UI 重構
- **損益視圖拆分左右**：「盤中快照」（即時 + 13:45 收盤 + intraday）和「當週累積」（即時 + weekly_sum）各一個下拉選單
- **互斥指示燈**：金色小圓點標示目前作用中的視圖，點擊圓點或文字標籤可切換
- **非 default 合約禁用當週累積**：只有 active_full 合約才能使用右邊下拉選單
- **當週累積顯示起止時間**：開始時間（合約成為 default 的時間點）靠右與損益兩平同行，結束時間跟隨資料時間即時更新
- **Zoom 切換按鈕**：圖表左下方新增「全範圍 / ATM±4%」切換，預設全範圍
- **數字等寬**：toolbar 和 pnl-stats 加 `tabular-nums`，避免數字變化時欄位偏移
- **下拉選單只顯示全日盤快照**：日盤快照 JSON 本地保留但不出現在前端

#### 後端修正
- **夜盤 00:00~05:00 快照和 price log 遺漏**：`_is_trading_hours` 和 `_is_intraday_snap_time` 加入夜盤後半段判斷
- **13:45 快照 label 精簡**：移除「(全日盤)」括號
- **weekly_sum label 精簡**：改為「當週累積」
- **start_time 動態計算**：`/api/weekly-pnl` 回傳合約成為 default 的起始時間，prev_settle 在未來則不顯示
- **Toolbar 不換行**：`flex-wrap: nowrap` + gap 縮小

#### 資料清理
- 刪除已結算 TXUN04 intraday 快照
- 刪除資料汙染的 TX2N04 04-07 13:45 快照

---

## v5.1 (2026-04-07)

### Intraday 快照修正 + 前端自動刷新

#### Bug 修正
- **已結算合約不再存 intraday 快照**：結算日夜盤 (>=15:00) 自動跳過已結算系列（如 TXUN04 04-07 結算後夜盤不再存）
- **快照下拉選單過濾修正**：intraday 快照不再套用 week_str 日期過濾，修復月選等跨週合約被誤濾的問題
- **收盤快照 week_str 起點調整**：從 `prev_settle + 1 天` 改為 `prev_settle`，包含結算日當晚的夜盤資料
- 清理已結算 TXUN04 的無效 intraday 檔案

#### 前端改善
- **Server 重啟自動 hard reload**：前端偵測 `boot_id` 變化，自動 `fetch({cache:'reload'})` + `location.reload()`，不再累積瀏覽器分頁
- **下拉選單自動刷新**：每 1 分鐘自動重抓快照列表，新的 intraday 快照不需手動刷新即可出現

#### 啟動流程
- `stop.bat` 移除 `pause`，不再卡住 bash
- `scripts/start.py` log 檔改 `open('w')` 覆寫、移除自動開瀏覽器、加 `--no-access-log`

---

## v5.0 (2026-04-07)

### 盤中定時快照 + 分鐘價格線（策略數據基建）

#### 盤中快照（`snapshots/intraday/`）
- 日盤 09:00~13:30 / 夜盤 15:30~00:00，每 30 分鐘對齊整點（:00 和 :30）觸發
- 所有追蹤中的 full series（帶 N）全存，不限 active
- 夜盤只存 full series（日盤系列凍結無意義）
- 內容：完整 columnar table + pnl + raw_calls/raw_puts + atm + implied_forward + `futures_price`
- `has_data` 保護：沒有實際交易資料不存空檔
- 檔名格式：`{series}_{YYYY-MM-DD}_{HHMM}.json`

#### 分鐘價格線（`monitor/price_log_{YYYY-MM-DD}.csv`）
- 每分鐘對齊整分鐘記錄 FITX 現價 + implied_forward
- 交易時段自動判斷（日盤 08:45~13:45 / 夜盤 15:00~00:00），盤外不記
- 格式：`timestamp,futures_price,implied_forward`，~70KB/天

#### 策略計畫文件
- `docs/STRATEGY_PLAN.md`：Covered call 策略四階段完整計畫
- `docs/PROGRESS.md`：Phase 1~4 checklist 進度表

---

## v4.19 (2026-04-07)

### 雜項修正 + 測試腳本入庫

- **weekly-snapshot log 檔名修正**：log 訊息從硬編碼的 `{series}_{today}_weekly.json` 改為實際檔名（`os.path.basename(weekly_path)`）
- **weekly_sum compact JSON**：`_try_save_weekly_snapshot` 的 `json.dump` 補上 `separators=(',', ':')`，與日快照一致
- **測試腳本入庫**：`test/` 資料夾加入版控（bench_http、test_requests_session、test_snapshot_compress、test_snapshot_trigger、migrate_snapshots、dde_benchmark）

---

## v4.18 (2026-04-07)

### Log 檔清理：停止無限增長 + 移除遺留檔案

#### 問題
- `uvicorn.log`（HTTP access log）、`uvicorn_err.log`、`xqfap_err.log` 由 `start.py` 用 `open('a')` 導出，沒有 rotation，會無限增長（已累積 3MB + 0.3MB + 0.8MB）
- `uvicorn_err.log` / `xqfap_err.log` 的內容與 `server.log` / `xqfap.log`（有 RotatingFileHandler）完全重複
- `uvicorn.log` 記錄每個 HTTP request 一行，xqfap_feed 每秒打幾十次，增長最快

#### 修正（`scripts/start.py`）
- 4 個 `open('a')` → `open('w')`：每次重啟清空，不再累積。歷史紀錄由 RotatingFileHandler 的 `server.log` / `xqfap.log` 保管
- uvicorn 加 `--no-access-log`：停掉重複的 HTTP access log（`uvicorn.log` 從 3MB 降為 0 bytes）

#### 清理
- 移除 `monitor/` 下 6 個舊架構遺留檔案：`feed.log`、`feed_err.log`、`monitor_loop.log`、`monitor_night.log`、`monitor_night.py`、`ws_count.txt`（無任何 code 引用）

---

## v4.17 (2026-04-07)

### 快照檔案壓縮：table 改用 columnar 格式

#### 問題
- 快照檔 ~100KB，其中 `table`（T字報價表）佔 88%（~94KB）
- 126 行 × 34 個欄位，key 名稱如 `"ask_match_call_day"` 重複 126 次

#### 解法：行列轉置（columnar）
- 存檔時 `table` 從 `[{key:val,...},...]`（list-of-dicts）轉為 `{key:[val,...],...]}`（dict-of-lists）
- 每個 key 只出現一次，資料完全無損，可雙向轉換
- `/api/snapshots/{filename}` 讀取時自動偵測格式，columnar 轉回 rows 再回傳前端（前端零改動）
- 同時改用 compact JSON（`separators=(',',':')` 去除多餘空格）

#### 效果
- 全部 12 個快照：1,161KB → 357KB（**-69%**）
- 單檔：~100KB → ~30KB
- 舊格式快照向後相容（API 自動偵測 list/dict）

#### 遷移
- 既有快照一次性轉換完成

---

## v4.16 (2026-04-07)

### 修正：force-snapshot 少存 weekly_sum + 空殼擋住自動快照

#### 問題
1. `force-snapshot` 不產生當週全日盤累積（weekly_sum），結算日手動重建快照時只有日快照
2. `force-snapshot` 無論資料是否完整都標記「今天已存」，資料不完整時會擋住後續自動快照

#### 修正（`main.py` `/api/force-snapshot`）
- 結算日（`settlement_date == today`）時補呼叫 `_try_save_weekly_snapshot`
- 只在 store 有實際交易資料（`has_data`）時才設 `_snapshot_taken_today`，空殼不標記

---

## v4.15 (2026-04-07)

### 當週全日盤累積快照只在結算日存檔

- `_try_save_weekly_snapshot` 只在 `settlement_date == today` 時才呼叫
- 非結算日資料持續變動，看即時(當週全日盤累積)即可，不需存檔
- 清理 4 個非結算日的 weekly_sum 快照

---

## v4.14 (2026-04-07)

### 修正：結算日快照無法自動觸發

#### 問題
- 結算日當天，13:45 一到 `_settled = True`，`api_feed` 停止更新 `_last_updated`
- `_last_updated` 凍在 ~13:44:xx，永遠達不到快照門檻 13:45:20
- 結算日的快照從來不會自動存檔，每次都需要手動 force-snapshot

#### 修正（`main.py` `_try_save_snapshot`）
- 時間判斷從 `_last_updated` 時間改為 `now`（現在時間 >= 13:45:20 即觸發）
- 資料日期判斷：正常靠 `_last_updated` 是今天；結算日 fallback 到 `settlement_date == today`（涵蓋重啟後 `_last_updated` 為 0 的情況）
- 新增 `has_data` 保護：store 裡至少要有一筆 `avg_price > 0` 或 `net_position != 0`，避免 init 後 bulk_req 還沒跑完就存空殼快照

#### 測試
- 結算日（2026-04-07 TXUN04）實測：刪除快照 → 重啟 server → 15 秒內自動觸發
- 快照內容完整：126 strikes、87 raw_calls、92 raw_puts、ATM=33100

---

## v4.13 (2026-04-07)

### HTTP 連線重用 + file handle 洩漏修正

#### `xqfap_feed.py`：`requests.Session()` 連線重用
- 新增全域 `_http = requests.Session()`，所有 `requests.post()` / `requests.get()` 改為 `_http.post()` / `_http.get()`（共 12 處）
- 原本每次 HTTP 呼叫都建立新的 TCP 連線；改用 Session 後 keep-alive 重用連線，省掉 TCP 三次握手
- `requests.Session` 底層 urllib3 的 `HTTPConnectionPool` 是 thread-safe，多執行緒共用無問題；server 重啟後 stale connection 也能自動恢復

#### 效能實測（bulk_req 含 DDE 讀取 + HTTP 推送）

| 系列 | 舊版全日盤 | 新版全日盤 | 舊版日盤 | 新版日盤 |
|------|-----------|-----------|---------|---------|
| TXUN04 (252筆) | 10.5s | **4.1s** | 24.4s | **6.2s** |
| TX2N04 (240筆) | 10.4s | **2.0s** | 24.7s | **3.7s** |
| TXVN04 (236筆) | 10.4s | **2.3s** | 23.9s | **4.8s** |
| TXON04 (356筆) | 10.4s | **10.1s** | 38.9s | **13.0s** |

全日盤平均加速 ~3x，日盤平均加速 ~4x。

#### `main.py`：`restart_feed` file handle 洩漏修正
- `open(log_path, 'a')` 改用 `with` 語句，`Popen` 啟動後自動關閉 file handle（原本每次 `/api/restart-feed` 洩漏一個 handle）

---

## v4.12.2 (2026-04-04)

### 改善：start.bat 自動安裝依賴套件

- 啟動時自動執行 `pip install -r requirements.txt`
- 新環境雙擊 `start.bat` 即可直接使用，無需手動裝套件
- 已安裝者無感（靜默模式，瞬間跳過）

---

## v4.12.1 (2026-04-04)

### 修正：補齊 requirements.txt 缺少的依賴套件

- 新增 `pywin32>=306`（DDE 連接新富邦 e01 必要）
- 新增 `requests>=2.28.0`（xqfap_feed.py 依賴）
- 新環境執行 `pip install -r requirements.txt` 即可一次裝齊，不再需要手動補裝

---

## v4.12 (2026-04-02)

### 修正：DDEML thread-local instance 洩漏 + test mode cleanup 順序

- **`_req_thread` 斷線路徑（Path1 / Path2）**：原本斷線時只清 `hconv`，`inst` 仍留在 `_thread_local`；下次 lazy init 重建連線時 `_thread_ddeml_connect()` 直接覆蓋 `_thread_local.inst`，舊的 DDEML instance 永遠沒有 `DdeUninitialize`，造成 Windows DDEML 系統資源累積洩漏。修正：兩條斷線路徑均補上 `DdeUninitialize(old_inst)` + `_thread_local.inst = None`
- **`_ddeml_worker`（--test-ddeml 模式）**：`finally` 區塊 `hconv` 存在時原本直接 `pass`，改為正確呼叫 `DdeDisconnect(hconv)`，符合 DDEML cleanup 順序（先 Disconnect 再 Uninitialize）

---

## v4.11 (2026-04-02)

### 修正：watchdog 重連後 TotalVolume 資料遺失（深度 ITM 合約成交量空白）

- **問題根源**：DDEML watchdog 每 60~130 秒因 DDE 斷線而重連。`_reconnect_and_resubscribe()` 重訂 advise 後沒有補跑 `_bulk_request_series`，而 ADVSTART 語義只訂閱「未來變動」，不回送當前值；斷線期間的所有 TotalVolume / InOutRatio 變動因此永久遺失
- **症狀**：深度 ITM Call（如 04F1 的 32250~32400）的外盤成交量、內盤成交量、成交量欄位全部顯示空白，`inout_ratio` 始終停在初始預設值 50.0
- **修正（`xqfap_feed.py`）**：`_reconnect_and_resubscribe()` 在 `_advise_subscribe()` 完成後，立即啟動 daemon thread 執行 `_bulk_request_series(target)`，回補斷線期間遺失的資料

---

## v4.10 (2026-04-01)

### 修正：server 重啟後快照重複存檔問題

- **`main.py` lifespan 快照掃描**：舊邏輯用 3-part 硬解析（`len(parts)==3`），無法識別 v4.8 改版後的新命名格式（`26_04W1_TX1N04_2026-04-01.json`），導致每次重啟 `_snapshot_taken_today` 始終為空，今天已存的快照會被重複覆寫
- 改用 `_parse_snap_filename()` 解析，格式相容且無需維護兩套邏輯

---

## v4.9 (2026-04-01)

### 快照存檔限制為 active 合約 + 清理無用快照

#### 修正
- **`_try_save_snapshot`**：加入 `series not in (_active_full, _active_day)` guard，只對當前 active 合約存快照，其餘已初始化的系列不再產生快照檔
- **刪除 17 個無用快照**：清除非 04W1（TX1N04/TX104）合約在其 week_start 之前累積的快照（TXUN04/TXU04/TXVN04/TXV04/TX2N04/TX204/TXON04/TXO04）

---

## v4.8 (2026-04-01)

### 快照檔命名規則統一

#### 新命名格式
- 日快照：`{YY}_{label}_{series}_{YYYY-MM-DD}.json`
- 週累積：`{YY}_{label}_{series}_{YYYY-MM-DD}_weekly_sum.json`

其中 `{YY}` = 結算年後兩位，`{label}` = 合約代號（如 `04W1`、`04F1`、`04`，由 `tf_name_label` 從 PREFIX_RULES 推導）

#### 範例
- `26_04W1_TX1N04_2026-04-01.json`（原 `TX1N04_2026-04-01_1345.json`）
- `26_04W1_TX1N04_2026-04-01_weekly_sum.json`（原 `TX1N04_2026-04-01_weekly.json`）

#### 實作
- `main.py` 新增 `_snap_prefix()`、`_snap_filename()`、`_parse_snap_filename()` 三個 helper
- 所有存/讀快照路徑（`_try_save_snapshot`、`_try_save_weekly_snapshot`、`api_snapshots`、`api_weekly_pnl`）改用新 helper
- 現有 21 個快照檔一次性改名完成

---

## v4.7 (2026-04-01)

### 日盤盤外資料時間固定 + 右側損益圖初始 zoom

#### 修正
- **日盤（未結算）盤外資料時間**：08:45 前顯示昨天 13:45:00；13:45 後顯示今天 13:45:00；盤外不更新 `_last_updated`（heartbeat 與 `/api/update` 兩條路都封）
- **日盤盤外斷線偵測**：非 08:45~13:45 時段跳過 feed-dead toast，不誤報斷線

#### 新功能
- **損益圖初始 zoom**：切換合約/盤別時，自動 zoom in 到 ATM ±4%（取最近百位數，例如 ATM=33400 → 32100~34800），slider 同步縮到該範圍；可手動拖 slider 擴大

---

## v4.6 (2026-04-01)

### 結算後合約穩定性 + 盤後 watchdog 靜默

#### 問題
1. 已結算合約（04W1）資料時間持續刷新，不應變動
2. 即時(當週全日盤累積) 與 2026-04-01 當週全日盤累積 圖表不一致（double count）
3. 盤後 watchdog 因 DDEML 不推 callback 而每 3 分鐘觸發 reinit，導致畫面反覆斷線出不來
4. 合約下拉選單在 watchdog reinit 循環中永遠空白（`_post_contracts` 被 push_snapshot 卡住）
5. 盤後重啟後 active 合約選到已結算的 TX1N04 而非 TXUN04
6. 日盤（TX104 等）切換後 bid/ask/成交價全空（Phase 2 bulk_req 未抓這三個欄位）

#### 修正
- **`main.py`**
  - `_series_last_updated(series)`：已結算合約固定回傳 `{結算日} 13:45:00` timestamp，兩條回傳路徑（`api_feed`/`api_status`）統一使用
  - `/api/update`：已結算合約不更新 `_last_updated`（封堵 heartbeat 以外的刷新路徑）
  - `/api/heartbeat`：已結算合約直接 return，不更新時間戳
  - `api_weekly_pnl`：已結算合約直接回傳 `{series}_{date}_weekly.json` 並標記 `_settled: True`，避免因 live_strikes 漂移重算導致曲線不一致
- **`static/app.js`**
  - `_mergeWithLive`：baseline 帶有 `_settled` 旗標時直接回傳 baseline，不疊加 live_pnl（修正 double count）
  - `setInterval` 斷線偵測：已結算合約（`_activeSettlementDate <= today` 且 >= 13:45）跳過 feed-dead toast
  - `updateStatus`：從 `status.settlement_date` 更新 `_activeSettlementDate`
- **`xqfap_feed.py`**
  - `_advise_loop` WM_TIMER handler：盤後（14:30~08:30）跳過 watchdog 重連，不再觸發 reinit
  - 初始化 loop：`_post_contracts` 移至 `_post_init` 完成後立刻呼叫，不等慢速 push_snapshot（修正 contracts 空白）
  - `_reinit` 切換 active：after_cutoff 後跳過已結算合約，選第一個未結算 full series
  - Phase 2 `_bulk_request_series`：`_worker_day` 補抓 `Bid/Ask/Price` 三個欄位（修正日盤報價空白）

---

## v4.5 (2026-04-01)

### 即時(當週全日盤累積) baseline 算法重寫：虛擬孿生 T 字報價表

#### 問題
舊算法（`_union_pnl`）將每天快照的 pre-computed `pnl[]` 直接對齊 strike union 後相加，缺失的 strike 填 0，導致曲線在各快照 strike 邊界出現明顯懸崖與鋸齒（例：settlement=30850 不在 Day-2 快照 → 填 0 → 從 30800 的 -0.017 跳到 0 再跳回 30900 的 -0.0168）。

#### 解法：虛擬孿生算法（`_virtual_twin_pnl`）
- **全域 settlement 軸**：以今天 live strike 列表作為唯一的 x 軸（最完整），所有歷史快照統一在此軸上計算。
- **重新計算而非查表**：對每個歷史快照，用其原始部位（`raw_calls`/`raw_puts`）在「今天全部 strike」重算損益，而非查 pre-computed `pnl[]`。某天不存在的 strike 自然不貢獻（乘以 0），但其他 strike 的 intrinsic 仍連續遞增 → 曲線平滑。
- **`baseline = Σ pnl_d(settlement)`**（各天重算後加總）；再加 live pnl = 完整當週累積。

#### 格式升級
- 快照新增 `raw_calls`/`raw_puts` 欄位：每筆 `{strike, net_pos, avg_price}`，供新算法重算使用。
- `_try_save_snapshot` 與 `api_force_snapshot` 均同步更新。
- 舊快照 backward compat：自動從 `table` 欄位補填 `raw_calls`/`raw_puts`（migration 腳本內建於 `_virtual_twin_pnl` fallback）。

#### 效果
- 修正前最大跳變：29200→29300 diff=5.64 億（懸崖）
- 修正後最大跳變：37700→37800 diff=1.55 億（正常線性遞增，無懸崖）
- 低端 28500~29400 每 100 點差值均勻約 0.81 億，完全平滑

---

## v4.0 (2026-03-31)

### 週累積損益快照功能（即時(當週全日盤累積)）

#### 新增功能
- **快照機制**：每日 13:45:20 後自動存快照（`snapshots/{series}_{date}_1345.json`），含 strikes/pnl/table/atm_strike/implied_forward；全日盤（TX1N04 等）與日盤（TX104 等）各自獨立存檔
- **損益視圖下拉選單**（工具列）：
  - 即時 (當盤)：現有即時功能不變
  - 即時 (當週全日盤累積)：全日盤歷史快照加總（baseline）+ 當盤 live pnl 永遠疊加；14:35 規則決定 baseline 是否含今天快照（14:35 前不含，14:35 後含）
  - 個別快照（YYYY-MM-DD HH:MM）：顯示歷史快照的損益圖與 T 字報價表
- **`/api/snapshots`**：列出快照 metadata
- **`/api/snapshots/{filename}`**：取得單張快照完整資料
- **`/api/weekly-pnl?series=&settlement_date=`**：回傳合約 active 期間的快照加總
- **`/api/force-snapshot?series=`**：強制用記憶體資料重建快照（繞過時間限制）

#### 當週定義
- `week_start` = 前一張合約結算後隔天（用 `taifex_calendar` 動態計算，正確處理清明等連假）
- 今天快照納入規則：14:35 前排除（live 代表今天）；14:35 後納入（XQFAP 重整後 live 已是新合約）
- 模擬驗證：50 個測試案例全過（含清明連假、單日合約、week_start 落假日等邊界條件）

#### UI 行為
- 切到快照或當週累積 → 全日盤/日盤按鈕藍底消除
- 切回即時(當盤) → 恢復全日盤藍底（預設）
- 合約切換（手動或伺服器自動推新 series）→ 損益視圖重置為即時，防止舊合約 baseline 混入

#### 合成期貨修正
- 日盤資料在 13:45 凍結，`calc_atm` 改用 `center_price=0`（two-step 從選擇權本身推算），不再用夜盤即時期貨價造成窗口偏移
- `_effective_price` 新增 bid/ask fallback：MM 下線且 last_price=0（server 重啟後）仍可計算

#### 樣式
- 全日盤/日盤切換按鈕改為橘黃色（與快照選單視覺一致）

---

## v4.4 (2026-04-01)

### 日盤 T 字表報價即時同步修正

#### Bug 1：日盤 成交價（last_price）不更新
- **根因**：`_fetch_one_changed` 對 day series（TX104）只抓 `TF-TotalVolume` / `TF-InOutRatio` / `TF-AvgPrice`，沒有抓 `TF-Price`
- **修法**：`xqfap_feed.py` `_fetch_one_changed` 補抓 `TF-Price` for day symbol，並將 `last_price` 加入 `day_item`

#### Bug 2：委買/委賣 歸零後仍顯示舊值（報價消失無法清除）
- **根因**：`FeedItem.bid_price` / `ask_price` / `last_price` 預設值 `0.0`，server 端條件 `if u.bid_price > 0` 擋住了「bid 消失（DDE 回空 → `_to_float` → 0.0）」的更新
- **修法**：`main.py` `FeedItem` 三個欄位 default 改 `-1.0`（與 `inout_ratio` 一致）；server 條件改 `>= 0`，允許 0.0 覆蓋舊值

#### Bug 3：日盤 委買/委賣/成交價 不即時更新
- **根因**：`_quote_poll_worker` 只 poll active full series（TX1N04），TX104 從未被輪詢；日盤的委買委賣在快照後就凍結
- **修法**：`xqfap_feed.py` quote_poll 每個 cycle 結束後，將 bid/ask/last 映射到對應 day series 同步推送（零額外 DDE request，直接複用已取得的值）
  - 時間窗口：**08:45~13:45 才 mirror**（日盤時間內三欄位完全一致）；13:45 後停止，兩邊各自獨立，避免 XQFAP 重整後夜盤值污染日盤最後成交

---

## v4.3 (2026-04-01)

### T 字表 Excel 複製 + 合成期貨對齊修正

#### T 字表改為真正 `<table>` 結構
- `static/index.html` — `#table-header` div → `<thead><tr><th>`；`#strike-table-body` div → `<tbody>`
- `static/style.css` — 移除 flex row layout，改用 `table-layout: fixed`；`thead th` 加 `position: sticky` 固定表頭；捲動移至 `#table-scroll`
- `static/app.js` — `_cell()` 改建 `<td>`；`_barCell()` 改建 `<td>` 含內層 `div.bar-wrapper`，用 `td._bar` 直接 reference 取代脆弱的 `firstChild` 遍歷；row 改建 `<tr>`
- 效果：Ctrl+A → Ctrl+C → 貼 Excel 自動帶欄位結構與顏色；選特定文字複製不受干擾

#### Hover 底色不帶入 Excel
- `.row:hover` 改用 `box-shadow: inset 0 0 0 1000px #1c2128`，視覺不變；Excel 貼上時忽略 box-shadow，非 ATM row 不再帶底色

#### 快照模式不觸發資料饋送中斷 Toast
- 純快照模式（靜態資料）跳過 feed-dead 檢查；週累積模式維持檢查（仍有 live pnl）

#### 合成期貨 15 格對齊 ATM 修正
- `core/calculator.py` — `calc_atm` 改為兩步驟：先用 `center_price` 找初步中心取 ±7 算出 implied/atm，再以 **atm 為真正中心** 重新取 ±7 重建 `synthetic_map`；修正前 `_futures_price` 偏離 ATM 時，合成期貨欄位會偏向期貨價而非真實 ATM

#### ATM 初始捲動修正
- `updateTable` 改用 `requestAnimationFrame` 確保整批 DOM 更新完成後再執行 `scrollIntoView`，避免資料還沒全部寫入時就計算捲動位置

---

## v4.2 (2026-04-01)

### 資料饋送中斷 Toast 修正

#### 問題
- 切到個別快照（非即時類）時，`_serverLastUpdated` 停止更新，90 秒後仍會觸發「⚠ 資料饋送中斷」toast 並嘗試重啟 xqfap，但快照資料完全靜態，xqfap 狀態根本無關
- 原本把週累積也一起豁免，但週累積有 live pnl 即時疊加，xqfap 掛掉會影響數據，不應豁免

#### 修正
- `static/app.js` — feed-dead 檢查改為只在 `_viewMode === 'snapshot'` 時 bail out（同時收起 toast）；`live` 與 `weekly` 模式維持原本的 90s 檢查與自動重啟

---

## v4.1 (2026-03-31)

### Hotfix：快照觸發時機修正 + 日盤 heartbeat 補漏

#### 問題
- 快照門檻為 `>= 13:45:00`，收盤最後一筆資料可能在 13:44:xx 進來，導致快照不觸發
- `xqfap_feed.py` bg_poll heartbeat 只更新全日盤（TX1N04）的 `_last_updated`；日盤（TX104）在收盤後 DDE advise 靜止，`_last_updated` 停在 13:44:xx，快照同樣無法觸發

#### 修正
- `main.py` — `_try_save_snapshot` 門檻改為 `>= 13:45:20`，多留 20 秒等收盤資料完整推入
- `xqfap_feed.py` — bg_poll heartbeat 同時對 `day_series`（TX104 等）發送，確保 14:00 左右 `_last_updated` 仍會被更新至 >= 13:45:20

---

## v3.17.1 (2026-03-31)

### Hotfix：修正 compute_payload() UnboundLocalError

#### 問題
- v3.17 改動 `main.py` 時，將 `settlement_date=settlement` 加入 `calc_atm()` 呼叫（第 66 行），但 `settlement` 變數的賦值在第 70 行才出現，導致 `UnboundLocalError`，`/api/data` 全面回傳 500，頁面空白

#### 修正
- `main.py` — 將 `settlement = _settlement_dates.get(active_key, "")` 移至 `compute_payload()` 頂部（`calc_atm()` 呼叫之前）

---

## v3.17 (2026-03-31)

### 合成期貨計算：正確處理 Market Maker 下線時段

#### 問題
- `_effective_price()` 雖然在 bid/ask=0 時會 fallback 到 last_price，但若 XQFAP 在 MM 下線後短暫保留舊的非零 bid/ask（如快照殘留），仍會錯誤使用中間價計算 Put-Call Parity

#### 修正
- `core/calculator.py` — 新增 `_is_mm_online(settlement_date)` 函數，明確判斷兩個 MM 下線時段：
  - **夜盤深夜 02:00 ~ 開盤前 08:45**：流動性極低，MM 已撤單
  - **結算日 12:30 後**：MM 在結算前下線
- `_effective_price(o, mm_online)` 新增 `mm_online` 參數：
  - `mm_online=True` 且 bid/ask 皆有效 → `(bid + ask) / 2`
  - `mm_online=False` 或 bid/ask 任一為 0 → `last_price`（兩道保險）
- `calc_atm()` 新增 `settlement_date` 參數，傳入後計算 `mm_online` 狀態
- `main.py` — `compute_payload()` 將已有的 `settlement` 傳入 `calc_atm()`

---

## v3.16 (2026-03-31)

### 全日盤（TX1N04）優先 ready，啟動後 ~14s 即可顯示預設畫面

#### 問題
- 啟動後預設合約（04W1 全日盤）需等約 33 秒才能顯示，體驗偏慢
- 根本原因：`_bulk_request_series()` 在同一批 worker thread 中交錯拉取 full_series（6 fields）與 day_series（3 fields），兩者全部完成後才同時發 series-ready，導致 TX1N04 被 TX104 拖慢

#### 修正
- `xqfap_feed.py` — `_bulk_request_series()` 改為兩階段設計：
  - **Phase 1**：worker threads 只 REQUEST full_series（全日盤，6 fields） → POST feed → `series-ready TX1N04`
  - **Phase 2**：worker threads 只 REQUEST day_series（日盤，3 fields） → POST feed → `series-ready TX104`
  - TX1N04 在 Phase 1 完成後立即 ready，無需等待 TX104
  - 抽出 `_cleanup_thread()` helper，兩階段共用 DDEML 資源釋放邏輯
- `xqfap_feed.py` — `main()` 啟動迴圈：將 `Thread(_bulk_request_series).start()` 移至 `_push_snapshot(meta_full)` 之後、`_push_snapshot(meta_day)` 之前，讓 Phase 1 與 TX104 push_snapshot 並行執行

#### 效果（三次平均）
| 合約 | v3.15 | v3.16 | 改善 |
|------|-------|-------|------|
| TX1N04（全日盤，DEFAULT） | +33.3s | **+14.1s** | **-19.2s（-57%）** |
| TXUN04 | +33.5s | +25.2s | -8.3s |
| TX2N04 | +33.5s | +43.3s | +9.8s（trade-off） |
| TXON04 | +39.3s | +53.3s | +13.9s（trade-off） |
| ALL READY | ~39.5s | ~55.3s | +15.8s（trade-off） |

> 非預設合約稍微延後（Semaphore(1) 串行約束下的必要代價），換取預設畫面大幅提速。

---

## v3.15 (2026-03-31)

### 初始化期間封鎖 WS 渲染，防止右側面板在「載入中」時出現舊資料

#### 問題
- 頁面剛載入時，下拉選單仍顯示「載入中...」，但右側損益兩平、X/Y 軸、scrollbar 已充滿大量履約價資料
- 根本原因：`fetchContracts()` 是 async，在 `await fetch()` 等待 HTTP 回應期間，`_viewingNonLive` 仍為初始值 `false`，WebSocket 資料直接進入 `handleData` 渲染流程，填滿右側面板
- `fetchContracts()` 完成後雖會呼叫 `_setViewingNonLive()` → `_clearDisplay()` 清除，但使用者已看到一瞬間的亂資料

#### 修正
- `static/app.js`：新增 `_ready = false` 旗標（初始為 false）
- `handleData()`：在 `_snapshotMode` 檢查後加入 `if (!_ready) return`，封鎖初始化期間的所有 WS 渲染
- `fetchContracts()`：確定初始系列（live 或 non-live）之前設 `_ready = true`，之後的 WS 資料才正常處理

---

## v3.14 (2026-03-31)

### 切換非 live 合約：即時顯示連線中進度、清除殘留資料

#### 問題
- 切換到尚未載入完成（有點點）的合約時，右側損益圖的「價平」markLine 殘留舊值
- X/Y 軸範圍（如 29200～37800）殘留舊合約資料
- 下方 scrollbar 殘留舊合約範圍
- 右上角「XX 個履約價」殘留舊數字
- 狀態列仍顯示「已連線」，要等 1~2 秒後才變成「連線中」

#### 修正
- `static/index.html`：「個合約」改為「個履約價」（語意更精確）
- `static/app.js` — `_clearDisplay()`：
  - 清除 ATM 虛線：`markLine: { data: [] }`（ECharts merge mode 不會自動清除）
  - 重置 X/Y 軸：`min: 'dataMin', max: 'dataMax'`
  - 重置 noUiSlider：`updateOptions + set([0,1])`
  - 清零 `sub-count` 顯示
  - 重置 `_atmStrike = null`
- `static/app.js` — 新增 `_setViewingNonLive(series, contractData)` helper：
  - 整合設旗標、清畫面、立刻更新 UI 三步為一個函數
  - 點選當下立即計算進度百分比並顯示 `連線中(X%)`，不等 5 秒 poll
- `static/app.js` — `fetchContracts()` 兩處 call site 改用 `_setViewingNonLive()`
- `static/app.js` — `updateStatus()` 加入 `_viewingNonLive` guard，避免 WebSocket 覆蓋「連線中」狀態
- `static/app.js` — 5 秒 poll 新增進度更新邏輯（持續刷新百分比）
- `main.py` — `/api/contracts` 回應新增 `total_count`（已訂閱履約價數）與 `loaded_count`（bid/avg > 0 的筆數），供前端計算載入進度

---

## v3.13 (2026-03-31)

### 合約顯示邏輯重寫：3週選+1月選、15:00切換規則、月底跳月bug修正

#### 問題
- 月底（如3/31）啟動時，`timedelta(days=31)` 導致掃描月份從3月直接跳到5月，4月合約全部消失
- 啟動時只顯示 TXON05（5月月選），04W1/04F1/04W2/04 全部遺失
- 未實作「3個最近週選＋1個月選」的固定顯示規則
- 未實作 15:00 切換規則（結算日 15:00 後保留已結算合約、補入下一個、切換 default）
- 啟動 race condition：feed 比 uvicorn 早完成第一個系列的探索，`POST /api/init` 失敗後不重試，導致 default 系列設錯

#### 根本原因
- `taifex_calendar.py:build_scan_plan()` 和 `xqfap_feed.py:_scan_valid_series()` 皆用 `timedelta(days=31)` 推算下個月，月底日期加 31 天會跳過月份
- `xqfap_feed.py:main()` series 選取邏輯未依結算日排序，`[:3]` 切到的是前綴順序而非時間順序
- 無 15:00 切換邏輯
- `_post_init` 失敗後靜默跳過，uvicorn 起來後第二個系列搶先成為 active

#### 修正
- `taifex_calendar.py`：`build_scan_plan()` 月份計算改為 `((m-1) % 12) + 1` 正確跨月
- `xqfap_feed.py`：`_scan_valid_series()` 同樣修正月份計算
- `xqfap_feed.py`：series 選取邏輯全部重寫
  - `weekly_all` / `monthly_all` 依結算日排序後再切片
  - `after_cutoff = now.time() >= datetime.time(15, 0)`
  - 15:00 前：取3個 `sd >= today` 週選 + 1個月選
  - 15:00 後：保留今天結算的週選/月選 + 取3個 `sd > today` 週選 + 1個 `sd > today` 月選
  - default 系列排第一位確保 `main.py` 正確設為 active
- `xqfap_feed.py`：探索 loop 前加入 uvicorn 就緒等待（最多30秒輪詢 `/api/status`），解決 race condition

---

## v3.12 (2026-03-30)

### 兩段式顯示修正、系列切換 UX 改善

#### 問題
- 服務剛啟動時畫面呈現「兩段式」：先跑出少量不完整資料，過一陣子才完整更新
- 委買、委賣、成交價、合成期貨是第二階段才出現，點點消失後切入仍看不到這些欄位
- 切換到已 live 的系列時，舊畫面殘留 3-4 秒才更新
- 切換到未 live 的系列後，等點點消失仍停在空白畫面

#### 根本原因
- `calc_atm()` 在 `common` 為空時早返回只回 2 個值，但 `compute_payload()` 解包 3 個 → `ValueError` 導致每次廣播崩潰，是所有切換問題的主因
- `_bulk_request_series` 只抓 `TotalVolume / InOutRatio / AvgPrice`，未抓 `Bid / Ask / Price`，導致 series-ready 時委買/委賣/成交價全為 0
- `live` 旗標用 `_last_updated > 0` 判斷，snapshot 剛推完就變 true，bulk_req 尚未完成

#### 修正
- `core/calculator.py`：`calc_atm` 空 common 早返回改為 `return None, {}, None`（3 個值）
- `xqfap_feed.py`：`_bulk_request_series` worker 新增抓 `TF-Bid / TF-Ask / TF-Price` 並帶入 POST payload，確保 series-ready 時所有欄位齊全
- `main.py`：新增 `_series_ready: set`，`bulk_req` 完成後才標記 live；新增 `POST /api/series-ready` endpoint；`purge-series` 同步清除 ready 旗標
- `xqfap_feed.py`：`_bulk_request_series` 完成後 POST `/api/series-ready`，通知 backend
- `main.py`：`api_set_series` 計算完 payload 後廣播並在 HTTP response 一併回傳
- `static/app.js`：`fetchContracts` 初始化時依 live 狀態決定是否清空等待（與 onchange 一致）
- `static/app.js`：`handleData` 的 `_viewingNonLive` 守衛加入 `_contractsData.live` 確認，防止 bulk_req 完成前 WS 廣播誤觸發渲染
- `static/app.js`：`_switchSeries` 改 async，POST 回應直接 render，不等下一次 WS 廣播
- `static/app.js`：`_targetSeries / _currentActiveSeries` 原子置換防止舊 series 資料殘留

---

## v3.11 (2026-03-30)

### 背景系列啟動加速

#### 問題
- 重啟後背景合約系列（TXUN04、TX2N04、TXON04）要等約 65 秒才有資料
- 根本原因：舊邏輯背景系列只等 `bg_poll` 第一輪輪詢，而 `bg_poll` 有 `offset` 延遲（最多 15s）加上 `sleep(20)` 週期，合計最慢要等 35s+ 才開始第一次 bulk_req

#### 修正
- `xqfap_feed.py` 啟動迴圈：`i != 0` 的系列改為在迴圈內立即 `Thread(_bulk_request_series).start()`，不等 `bg_poll`
- `bg_poll` 維持原邏輯（active series 心跳、背景 series 每 20s 輪詢），作為後續定期刷新
- 實測：全部 4 個系列就緒時間從 ~65s 縮短至 ~44s

---

## v3.10 (2026-03-30)

### 合成期貨改 15 檔、預估結算價顯示、圖表修正

#### 合成期貨計算改為 15 檔（中心±7）
- `calc_atm()` 改為先找最近中心履約價，再向上/下各取 7 檔（共 15 檔，原為任意最近 10 檔）
- 回傳新增 `implied_forward`：15 檔 F_K 平均值四捨五入至整數

#### 預估結算價顯示
- pnl-stats 第三行「預估結算價」改為顯示 `implied_forward` 實際計算值
- 顏色使用 `#ffa657`（橘黃，與合成期貨欄一致），新增 `.stat-atm` CSS class
- `main.py` payload 新增 `implied_forward` 欄位

#### 圖表修正
- 圖表 ATM 虛線標籤由「ATM」改為「價平」
- grid top padding 從 20 → 40，修正標籤被裁切的問題

---

## v3.9 (2026-03-30)

### 閃爍框行為修正、synthetic/pnl 補上 flash

#### 閃爍框根治
- 改用 JS setTimeout 取代 CSS animation 控制閃爍，消除 animation restart 造成的視覺不一致
- `_updateCell`：改用 `el._baseCls` 儲存 base class，更新 className 時保留 flash class，避免 flash 被 className 覆寫清除
- Flash 計時策略改為「第一次觸發才啟動 2s timer，計時中不重設」，確保不論更新頻率高低一律亮 2s 後熄滅
- `_cell`（row 建立路徑）補上 setTimeout，修正 flash class 寫入後永不熄滅的 bug
- `_clearDisplay` 同步清空 `prevValues`，修正合約切換後所有欄位同時 flash 且不熄滅的 bug
- CSS 移除 `@keyframes flash-change` animation，`.flash` 改為單純 outline 樣式

#### synthetic / pnl 欄位補上 flash
- `synthetic`、`pnl_call`、`pnl_put`、`pnl_combined` 改用 `_updateCell`，補上變化偵測與 flash
- `prevValues` 追蹤加入 `syn`、`pc`、`pp`、`pcomb`

---

## v3.8 (2026-03-30)

### 全欄位更新架構重整、race condition 修正

#### 架構調整
- `_quote_poll_worker` 回歸只輪詢 Bid/Ask/Price（3 欄），避免與 ADVISE 路徑競爭成交量欄位
- `_QUOTE_POLL_THREADS` 提升至 24，移除 `_QUOTE_POLL_INTERVAL` sleep，讓工作本身作天然節流（實際間隔 ~350ms）
- `_bg_poll_one_series` 改為純心跳（POST `/api/heartbeat`），不再做全量 REQUEST，消除 bulk_req 造成的 stale data 覆蓋問題
- 新增 `/api/heartbeat` 端點，僅更新 `last_updated` 時間戳不觸發廣播

#### Race condition 修正
- 修正 `_bg_poll_one_series` / `_bulk_request_series` 長達 4~5s 的全量輪詢與 ADVISE 路徑互相覆蓋，導致成交量偶爾短暫跳回舊值的 bug
- 修正 `main.py` `/api/feed`：`trade_volume=0`（quote-only 更新）時完全跳過 inout_ratio / bid_match / ask_match 重算，避免因整數反算造成 InOutRatio 誤差 flash

#### 現況更新頻率
- 委買/委賣/成交價：~350ms（quote_poll 輪詢）
- 成交量/內外盤比/均價/淨CALL/淨PUT/損益曲線：即時（ADVISE 有成交即觸發）
- 合成期貨 F_K / FITX 現價：~350ms（quote_poll 每輪結束後推送）

---

## v3.7 (2026-03-30)

### 委買委賣成交價即時刷新、T字表標題修正、價格格式化

#### 委買/委賣/成交價即時刷新
- 新增 `_quote_poll_worker`：每 0.5s 並行輪詢全部 active symbols 的 `TF-Bid`/`TF-Ask`/`TF-Price`
  - 使用 4 條 DDEML thread 並行，與 `_quote_prevs` 比對只推有變化的合約
  - 解決原本「無成交時 bid/ask/last 完全不刷新」的問題
- `main.py` `/api/feed`：bid/ask/last 有實際變化時也遞增 `value_changed`，觸發廣播

#### FITX 現價 / 合成期貨 F_K 改為每 0.5s 刷新
- `_quote_poll_worker` 每輪結束後呼叫 `_push_futures_price()`
- `/api/set-futures-price`：FITX 現價有變化時立即 `broadcast(compute_payload())`，F_K 隨之重算

#### T字表標題修正（還原 v3.5 原始標題）
- `外盤成交量(Buy Call)` / `內盤成交量(Sell Call)` 還原（v3.6 誤改為「買進/賣出」）
- `成交量` 還原（v3.6 誤改為「成交」）
- Put 側 `內盤成交量(Sell Put)` 移至 `外盤成交量(Buy Put)` 左側（左右對調）
- 委買/委賣/成交價字色改為 `#8b949e`（與成交量一致）

#### 價格格式化
- 委買/委賣/成交價 ≥ 50 時顯示整數（去除小數點），< 50 保留一位小數
- 新增 `_fmtPrice(v)` helper 統一處理六個價格欄位

---

## v3.6 (2026-03-30)

### T字表新欄位、ATM 虛線、合成期貨、自動捲動修正

#### T字表欄位擴充
- **Call / Put 各新增三欄**：`委買`（Bid）、`委賣`（Ask）、`成交價`（即時 last price）
  - 使用 DDEML（`_req_thread`）讀取以取得正確小數精度，修正 pywin32 截斷問題
  - 欄位顏色：委買綠、委賣紅、成交價橙
- **欄位標題調整**：外盤/內盤成交量改為「買進 (Call/Put)」、「賣出 (Call/Put)」

#### ATM 價平虛線
- 右側損益圖加入橙色垂直虛線，標示當下 ATM 履約價
- 僅首次載入或切換合約時自動捲動 T字表至 ATM 位置，之後不強制拉回

#### 合成期貨欄位
- T字表新增 `合成期貨 (F_K)` 欄，僅顯示參與 ATM 計算的 10 個履約價
- `F_K = K + C(K) - P(K)`，Put-Call Parity 合成期貨價格

#### ATM 計算演算法修正
- **選擇權價格來源修正**：改用 `(bid + ask) / 2`（market maker 在線時）或 `last_price`（夜盤/結算日），不再使用日內加權均價 `avg_price`
  - 修正後 F_K 各履約價收斂至 ~1 點內；修正前因 avg_price 混入不同時間成交，散布達 ~60 點
- **ATM 中心計算**：改用 FITX\*1 即時現價（`FITXN*1.TF-Price` via DDEML）為中心，取最近 10 檔；FITX\*1 未就緒時自動 two-step fallback（先對全部 common strikes 算出 rough implied forward 再取 10 檔）
- `calc_atm()` 回傳值改為 `(atm_strike, synthetic_map)` tuple
- `build_strike_table()` 新增 `synthetic_map` 參數，輸出 `synthetic_futures` 欄位

#### FITX 現價推送機制
- `xqfap_feed` 每批次結束後推送 FITX\*1 現價至 `/api/set-futures-price`（新端點）
- `_get_center_price()` 改用 DDEML 讀取 `FITXN*1.TF-Price`（精確），fallback pywin32 `FITX00`
- 修正：推送點從廢棄的 `_load_one_series` 移至實際 main flow（line 1431）

**變更檔案**：`core/calculator.py`、`main.py`、`xqfap_feed.py`、`static/app.js`、`static/index.html`、`static/style.css`

---

## v3.5 (2026-03-27)

### UI 顏色與閃爍動畫微調

- **最大獲利 / 最大損失顏色對調**：「最大獲利」後的數字改為紅色（`#f85149`），「最大損失」後的數字改為綠色（`#3fb950`）
- **閃爍動畫改用 outline**：從 `box-shadow: inset` 改為 `outline + outline-offset: -2px`，讓左右相鄰閃爍框之間有 4px 視覺空隙，不再相碰；100% 以 `rgba(..., 0)` 淡出取代硬切

**變更檔案**：`static/style.css`

---

## v3.4 (2026-03-27)

### T字表 hover 閃爍修正 & 細節

- **修正 hover row 每秒閃爍**：`updateTable` 改為原地更新（in-place update），保留 row DOM 節點（`body._rowMap`），不再每次清空重建。`:hover` 狀態不中斷，閃爍消除
- **Flash 動畫重觸發**：原地更新時以 `classList.remove → void offsetWidth → classList.add` 強制 reflow，確保資料真正變化時動畫正確重啟
- **資料時間格式**：加入秒數，改為 `YYYY-MM-DD HH:MM:SS`

**變更檔案**：`static/app.js`

---

## v3.3 (2026-03-27)

### UI 細節調整

- **T字表 PUT 側欄位**：全部改為靠右對齊（`text-align: right`），與 Call 側一致
- **成交量 / 內外盤% 欄位**：補齊 `font-size: 11px`，與其他數字欄統一
- **標題列**：全部置右（`!important` 覆蓋），含括號內容換至下一行（`<br>`）
- **閃爍動畫**：改為單色細框（`box-shadow: inset 0 0 0 1px rgba(139,148,158,0.55)`），持續 2 秒淡出；移除舊版橙色背景高亮
- **第二張快照**：新增 `03F4 20260327 14:56`（`snapshot_20260327_03F4.js`），點擊標籤後反白，點全日盤／日盤／更換合約自動退出快照模式
- **狀態列**：移除「WS Xs前」rx-age 顯示
- **資料時間格式**：統一為 `YYYY-MM-DD HH:MM`
- **分隔線預設位置**：`#left-panel` 寬度 `862px` → `880px`（貼近 Call損益欄左緣）

**變更檔案**：`static/index.html`、`static/app.js`、`static/style.css`、`static/snapshot_20260327_03F4.js`

---

## v3.2 (2026-03-27)

### 損益圖統計列

**圖表右側上方新增三行統計資訊：**
- 損益兩平：所有 pnl=0 的履約價（線性內插取整數，可多點）
- 最大獲利（萬，整數+千分位逗號）及對應履約價
- 最大損失（萬，整數+千分位逗號）及對應履約價
- 預估結算價：目前顯示 `--`，公式待定

**修正 max_pain 誤名**：`calc_combined_pnl()` 移除 `max_pain`/`max_pain_value`，更正 docstring 說明此曲線涵蓋 Call/Put 四方淨損益，非傳統 Max Pain。

**版面修正**：
- slider padding-top 52px（避免 tooltip 蓋住 x 軸「履約價」文字）
- slider padding-left 75px（對齊 ECharts Y 軸）
- ECharts grid top 縮至 20px（減少統計列與圖表間空白）

**變更檔案**：`core/calculator.py`、`static/index.html`、`static/app.js`、`static/style.css`

---

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
