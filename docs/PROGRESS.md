# 策略開發進度表

## Phase 1：資料基建

### 盤中定時快照
- [x] `_try_save_intraday_snapshot()` 函數（存所有 full series 到 `snapshots/intraday/`）
- [x] columnar + compact JSON + has_data 保護
- [x] 快照包含 `futures_price` 欄位
- [x] 對齊 :00 和 :30 整點觸發（日盤 09:00~13:30 / 夜盤 15:30~00:00）
- [x] 夜盤只存 full series
- [x] 在 test/ 寫測試，驗證快照觸發時機和內容正確性
- [x] 真實數據模擬測試（17:30 觸發，5 個 full series 全部正確）

### 分鐘價格線
- [x] 每分鐘對齊整分鐘追加 CSV（timestamp, futures_price, implied_forward）
- [x] 存放路徑：`monitor/price_log_{YYYY-MM-DD}.csv`
- [x] 判斷交易時段（日盤 08:45~13:45 / 夜盤 15:00~00:00），盤外不記
- [x] 在 test/ 寫測試
- [x] 真實數據模擬測試

### 整合測試
- [x] 重啟 server，確認盤中快照 + 分鐘線都正常運作
- [x] 確認收盤 13:45 快照邏輯不受影響
- [x] 確認前端功能不受影響
- [x] commit + push + changelog（v5.0）

---

## Phase 2：累積數據（4~6 週）

- [ ] 系統每天自動收集，無需人工介入
- [ ] 每週檢查一次資料完整性（有沒有漏存、空檔）
- [ ] 目標：6~8 個週選結算 + 1~2 個月選結算

---

## Phase 3：分析工具

- [ ] 牆特徵提取腳本（位置、厚度、距離 ATM、premium）
- [ ] 牆演變追蹤（跨日 + 盤中變化）
- [ ] FITX 分鐘線 × 牆位置疊圖
- [ ] 跨合約牆比較
- [ ] 當週累積曲線漸進變化（從 daily raw 重算）
- [ ] 統計分析：牆厚度 vs 結算是否被打穿
- [ ] 輸出報告

---

## Phase 4：Covered Call 決策規則

- [ ] 進場規則量化（牆厚度門檻 × premium 門檻 × 距離門檻）
- [ ] 持有監控規則（牆消退警告、價格逼近警告）
- [ ] 平倉/滾倉規則
- [ ] 前端輔助標記（建議賣點、牆強度指標）
- [ ] Paper trade 驗證

---

## 已完成

### 2026-04-07 Session
- [x] v4.13: HTTP 連線重用（requests.Session），bulk_req 加速 3~4x
- [x] v4.14: 修正結算日快照無法自動觸發
- [x] v4.15: 當週全日盤累積快照只在結算日存檔
- [x] v4.16: 修正 force-snapshot 少存 weekly_sum + 空殼擋住自動快照
- [x] v4.17: 快照 table 改用 columnar 格式（-69% 檔案大小）
- [x] v4.18: Log 檔清理（停止無限增長 + 移除遺留檔案）
- [x] v4.19: weekly-snapshot log 檔名修正 + compact JSON + 測試腳本入庫
- [x] 核心精神記錄：損益曲線 = 所有主動交易力量的淨損益
- [x] 策略方向討論：斜率牆、曲線不對稱度、breakeven 突破、covered call 選位
- [x] 完整 plan 文件撰寫
