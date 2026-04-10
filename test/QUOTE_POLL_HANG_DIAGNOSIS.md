# `_quote_poll_worker` 卡死診斷筆記

最後更新：2026-04-09 19:45（v5.4 之後立刻起的調查）

---

## 1. 觀察到的現象

2026-04-09 19:03 重啟 xqfap_feed 後：

```
19:03:47,253 [INFO] [quote_poll] 298 合約，298 筆變化，耗時 1969ms
（之後 96 秒沒有任何 quote_poll 訊息）
19:05:23,716 [WARNING] main: [feed-watchdog] TXVN04 資料凍結 96s，觸發 _do_restart_feed()
19:05:24      新 xqfap_feed 啟動
```

quote_poll 跑了**第一次**就成功完成 1969ms，然後**完全靜默 96 秒**直到 v5.4 加的後端 feed-watchdog 把它強制重啟。

## 2. 不是 deterministic bug

2026-04-09 19:34 又重啟一次 xqfap_feed 之後，quote_poll **連續跑 5 分鐘以上、155 次都沒卡死**（驗證時段 19:34~19:40）。

→ 結論：**非每次重啟都觸發**。是 timing-dependent 或依賴 DDE server 內部狀態的 bug。

## 3. Call chain 完整地圖

```
xqfap_feed.py:1695  threading.Thread(target=_quote_poll_worker, daemon=True).start()
↓
_quote_poll_worker (L722-802)
  └─ logger.info("quote poll worker 啟動")
  └─ ThreadPoolExecutor(max_workers=24, name='quote_req')
       └─ while True:
            ├─ series = _active_advise_series          # L743
            ├─ meta = _all_metas.get(series, {})       # L747
            ├─ symbols = list(meta.keys())             # L748
            ├─ futures = {executor.submit(_fetch_quote, sym): sym  # L754
            │             for sym in symbols}                       # 298 個 task
            ├─ for fut in as_completed(futures):       # L756 ← 沒有 timeout 參數
            │     try:
            │         symbol, ... = fut.result()       # 個別 catch
            │     except Exception:
            │         continue
            │     # mutation _quote_prevs[symbol] = cur （單 thread）
            │     changed.append(...)
            ├─ elapsed = time.time() - t0
            ├─ logger.info(f"[quote_poll] {len(symbols)} 合約...")  # 觀察點 ★
            ├─ if changed:
            │     _post_feed(changed, series)          # L778 — HTTP，timeout=5
            │     # mirror logic（日盤時段才會跑）
            │     if (...): _post_feed(day_changed, day_series)
            │     else: logger.debug(...)
            └─ _push_futures_price()                    # L802 — HTTP + DDE
       （沒有 outer try/except）
```

每個 worker 在 `_fetch_quote(sym)` 內做 6 個 `_req_thread()` 呼叫：

```
_fetch_quote (L730-738)
  ├─ _req_thread(f"{symbol}.TF-Bid")           # ★ 每個 5s timeout
  ├─ _req_thread(f"{symbol}.TF-Ask")
  ├─ _req_thread(f"{symbol}.TF-Price")
  ├─ _req_thread(f"{symbol}.TF-TotalVolume")
  ├─ _req_thread(f"{symbol}.TF-InOutRatio")
  └─ _req_thread(f"{symbol}.TF-AvgPrice")
```

`_req_thread` (L347-402)：
- thread-local `hconv`，lazy init via `_thread_ddeml_connect()`
- `DdeClientTransaction(hconv, ..., XTYP_REQUEST, _DDE_TIMEOUT_MS=5000, ...)`
- 失敗時 `DdeDisconnect + DdeUninitialize` 並把 `_thread_local.hconv = None`
- 內建 try/except 兜底

## 4. Hypothesis 排名

### H1（最有可能）— DDE server backpressure 導致每個 DDE call 都 5s timeout

**機制**：
- 24 個 worker thread × 298 symbols = ~12 symbols/thread
- 每個 worker 跑 12 × 6 = 72 次 `_req_thread`，每次最多 5s timeout
- 最壞情況：72 × 5s = 360 秒/thread
- 觀察到的 96 秒 ≈ 19 次 timeout 在最慢的 thread

**為什麼第一次成功**：剛重啟、fresh DDE connection、新富邦 e01 還沒被 24 條連線打到 backpressure
**為什麼非每次都復現**：取決於新富邦 e01 內部 DDE 狀態，可能跟前一個 process 留下的 zombie connection、或 DDE server thread pool 滿載有關

**證據**：
- ✓ 96 秒落在「19 次 timeout 在最慢 thread」的範圍
- ✓ `_req_thread` 失敗時靜默回傳 ''，**不會 log**，所以看不到 timeout 訊息
- ✓ `as_completed` 沒有 timeout 參數，會等到所有 futures 完成
- ✗ 沒有直接證據證明是 DDE backpressure（需要 DDE server side log）

### H2（次有可能）— 主迴圈 silent thread death（未捕獲例外）

**機制**：
- L748 之後到 L802 之前**沒有 outer try/except**
- 如果 `series.replace('N', '')`、`time.time()`、`logger.info`、`_post_feed`、`day_meta = _all_metas[day_series]`（KeyError 可能）任何一處 raise，整個 worker thread 死掉
- daemon thread + 沒設 sys.excepthook → 預設 Python 會把 traceback 打到 stderr
- xqfap_err.log 是 stderr 重導向，**但每次重啟用 'w' mode 開啟（start.py:60）會 truncate**，所以**原始 freeze 期間的 stderr 資訊已永久丟失**

**證據**：
- ✓ 從第一次 quote_poll log 後，後續 quote_poll log 完全消失
- ✗ 沒有看到後續任何「quote poll worker 啟動」之類的恢復訊息（因為沒有 supervisor 重啟它）
- ✗ 無法重現 / 無 exception 證據（已被 'w' mode 蓋掉）

### H3（不太可能）— ThreadPoolExecutor 內部 deadlock 或 as_completed bug
Python stdlib 經過大量測試，這層 bug 機率很低。但不能完全排除。

### H4（不太可能）— `_quote_prevs` dict 競爭
mutation 在主 thread 內，worker thread 不寫只 read。應該安全。

## 5. 阻塞排查方向

| 點 | 阻塞類型 | 是否會被 catch |
|---|---|---|
| `_thread_ddeml_connect()` 內 `DdeConnect` | 同步、無顯式 timeout | ❌ 連不上會回 NULL，不會卡 |
| `DdeClientTransaction(_DDE_TIMEOUT_MS=5000)` | 5 秒 timeout | ❌ 卡 5s 後失敗，但**單 worker 跑 72 次 ≈ 360s** |
| `as_completed(futures)` 沒 timeout | 等所有 futures | ❌ |
| `executor.submit()` 內部 queue | 無 backpressure | ❌ |
| `_post_feed(... timeout=5)` | HTTP 5s timeout | ✓ 有 try/except |
| `_push_futures_price()` 內 `_req_thread` + HTTP | DDE 5s + HTTP 2s | ✓ 有 try/except |
| 主迴圈未捕獲 exception | 立即拋出 | **❌ silent thread death** |

## 6. 修復方向（按風險由低到高）

### Fix A：加 outer try/except + 重啟 logging（純防禦，零風險）
在 `_quote_poll_worker` 的 `while True:` 內最外層加 try/except，捕獲所有例外、log 完整 traceback、`time.sleep(1)` 後 `continue`。

**效果**：
- 防止 H2（silent thread death）
- 下次 freeze 發生時，xqfap.log 會看到 traceback，root cause 現形
- 不改變正常路徑行為

### Fix B：加每次 iteration 的進入/退出 log marker（純觀測，零風險）
在 while loop 開頭和結尾各加一行 DEBUG/INFO log，記錄 iteration 序號 + 時間戳。
原本只在「成功完成一輪」後印 log，看不到「進入哪個階段就卡住」。

**效果**：
- 下次 freeze 時可以看到「進到 iteration N 但沒退出」，配合 elapsed 算出卡在哪
- 缺點：log volume 會大幅增加

### Fix C：加 as_completed 的 timeout（行為改動，需要測）
給 `as_completed(futures, timeout=30)` 加 30 秒 timeout。超時拋 `TimeoutError`，外層 catch 到後 cancel 掉所有未完成的 future、log 警告、`continue` 到下一次 iteration。

**效果**：
- 防止 H1（DDE backpressure 導致整個 iteration 卡幾分鐘）
- 但可能丟資料（被取消的 future 沒回來）
- 需要測試取消後 thread-local DDE 連線狀態是否需要清掉重建

### Fix D：用 `concurrent.futures.wait(..., timeout=N, return_when=ALL_COMPLETED)` 取代 `as_completed`（行為改動）
`wait()` 比 `as_completed` 更乾淨，能拿到 done/not_done 兩個 set。
非同步取消 not_done，記錄 stale worker 數量，重建 ThreadPoolExecutor 避免 stale thread。

### Fix E：完全重寫 quote_poll 為單純 sequential loop + retry（最大改動）
放棄 24 thread 並行，改成單 thread 順序 poll。每個 iteration 確定性地 ~10 倍慢（從 2s → 20s），但完全沒有 race。
**只有當 H1~H4 都修不了時的最後手段。**

## 7. 我的建議路徑

**Phase A**（純診斷，零 production 改動）：
1. 寫 mock-based reproduction 試 H1 和 H2 → `test/test_quote_poll_hang_repro.py`
2. 完整理解 `_req_thread` 在 DDE timeout 時的真實 latency
3. 在 test/ 目錄寫 instrumented 版本 of quote_poll worker 並用 mock DDE 跑 1000 次 iteration

**Phase B**（最小侵入修復，先做 Fix A）：
1. 只加 outer try/except + 完整 traceback log
2. 在 test/ 跑過 mock 重現確認新版能 catch H2 場景
3. 上線後等下次 freeze 自然發生，看 traceback 是哪一行

**Phase C**（依 Phase B 結果決定）：
- 如果 traceback 指向 silent exception → 修那個具體 exception
- 如果沒有 traceback（freeze 時間 90s+ 但無例外）→ 確認是 H1，做 Fix C 或 Fix D

**Phase D**（完整 production 部署）：
- 修好之後先在 test 環境模擬「DDE 變慢 → 變正常 → 變慢」循環
- 連續跑一個完整夜盤 headless 看 feed-watchdog trigger 次數能不能歸零

## 8. 暫時不考慮的選項

- **直接 patch production code 上線**：違反「想清楚再動」「test 目錄測試好才上線」的指示
- **更改 `_DDE_TIMEOUT_MS`**：5 秒已經夠短，再短會誤判 fresh start 期間的合法慢回應
- **改用 advise-only**：advise 本身就有漏接問題，不能反過來依賴它

## 9. Mock 重現結果（2026-04-09 19:53）

執行 `python test/test_quote_poll_hang_repro.py`（96 symbols, 24 threads, mock DDE timeout=1s 縮放）：

| Test | 情境 | 不加 Fix | 加 Fix A | 結論 |
|---|---|---|---|---|
| 0 | baseline normal | iter ~133ms | n/a | 正常 |
| 1/2 | H2: `_push_futures_price` iter≥2 拋 RuntimeError | **iter 2 後 thread 死亡，silent，沒人接** | **10 輪全跑完，9 個 exception 都 catch** | Fix A 對 H2 完全有效 |
| 3/4 | H1: iter≥2 所有 `_req_thread` sleep 1s | iter 2 卡 **24.0s** = 96/24 × 6 × 1s | 仍卡 **24.0s**（沒 exception 可 catch） | Fix A 對 H1 無效，需 Fix C/D |
| 5 | partial backpressure（1/8 symbol 變慢） | 每輪卡 5s 但不死 | n/a | 部分 backpressure 不會永久卡 |

### 把測試 elapsed 換算回 production 規模

production: **298 symbols, 24 threads, 5s timeout**
- per-thread workload: 298/24 ≈ 12.5 symbols × 6 fields = 75 calls
- 全卡 timeout 最壞: 75 × 5s = **375s/thread** → wall time = max thread = 375s
- 部分卡 timeout: 觀察到的 **96s** ≈ 25% calls timeout，符合 partial backpressure 模型

### 結論

1. **H1 (DDE backpressure) 是最可能的 root cause**：
   - 模型 elapsed 與觀察到的 96s 完全吻合
   - 測試重現模型沒問題
   - 但缺直接證據（DDE server side log）

2. **H2 (silent thread death) 不能排除**：
   - 測試確認 production code 真的會 silent die（loop body 無 try/except）
   - 但需要 traceback 證據才能定位是哪一行 raise
   - **xqfap_err.log 用 'w' mode 開啟，原始 freeze 期間的 stderr 已永久丟失**

3. **Fix A（outer try/except）是必做的下限**：
   - 防 H2，零風險
   - 順便提供 traceback 給未來的 freeze 事件，幫助確認 root cause
   - 對 H1 無效但**不會讓 H1 變更糟**

4. **Fix C/D（as_completed timeout）才能解 H1**：
   - 但需要更多測試 — 取消 future 後 thread-local DDE 連線狀態如何？
   - 取消後是否要重建 ThreadPoolExecutor？
   - 風險高，不在第一階段修

### 推薦的上線順序

1. **先修 `start.py:60` 的 'w' → 'a'**（log truncate bug）：1 行改，零風險，讓未來的 freeze 留下證據
2. **再修 production `_quote_poll_worker` 加 outer try/except**（Fix A）：~10 行改，零行為改動
3. **觀察 1~2 天**：看 watchdog 自動重啟次數、看是否有 exception traceback
4. **依結果決定**：
   - 看到 traceback → 修那個具體 bug，不需要 Fix C/D
   - 沒 traceback 但仍有 freeze → 確認是 H1，做 Fix C/D
5. **Fix C/D 一定要先在 test/ 完整模擬「DDE 卡住 → 取消 → 重建 → 恢復」 cycle 才能上線**

## 10. 風險意識

- 任何改動都不能讓「目前可運作的快照功能」變壞
- v5.4 的後端 feed-watchdog 是 last line of defense，改 quote_poll 時要確保 watchdog 仍能補救最壞情況
- xqfap_err.log 用 'w' mode 開啟導致 freeze 時的 stderr 永久丟失，**這個 bug 也應該順便修**（改成 'a' append mode 或 RotatingFileHandler），但這要動 `scripts/start.py`，先想清楚再動
