# OptionChart 開發計畫

## 專案目標
用富邦 fubon_neo SDK 取得台指選擇權（TXO）即時資料，
製作一個網頁版籌碼面板，仿照 `籌碼表範例.png`：
- 左側：各履約價的 淨CALL / 淨PUT 面積圖（T字報價表）
- 右側：買方合併損益曲線 + Max Pain 標注

公式詳見 `FORMULA.md`。

---

## 技術選型

| 層 | 技術 | 理由 |
|----|------|------|
| 資料來源 | fubon_neo SDK WebSocket | 不需開 e01，直連富邦，無 rate limit 問題 |
| 後端框架 | Python + FastAPI | 輕量，原生支援 WebSocket，async 友善 |
| 前端圖表 | ECharts | 支援面積圖、折線圖、標注線，文件完整 |
| 前端樣式 | 純 HTML/CSS/JS | 無框架依賴，部署簡單 |

---

## 檔案結構

```
~/OptionChart/
├── main.py              # FastAPI 主程式（entry point）
├── fubon_client.py      # 富邦 WebSocket 連線管理
├── calculator.py        # 所有計算邏輯（淨CALL/PUT、損益曲線、Max Pain）
├── config.py            # 帳號/憑證設定（不進版控）
├── requirements.txt     # Python 依賴
├── static/
│   ├── index.html       # 主頁面
│   ├── app.js           # 前端邏輯（WebSocket、ECharts）
│   └── style.css        # 樣式
├── FORMULA.md           # 公式與欄位定義
└── PLAN.md              # 本文件
```

---

## Phase 1 — 資料層驗證 ✅ 完成（2026-03-23）

### Step 1.1：安裝與登入測試 ✅
- SDK 從官網下載 wheel 安裝（PyPI 無此套件）
- 登入成功：帳號蔣承哲 / 296612 / futopt

### Step 1.2：取得近月 TXO 所有合約代碼 ✅
- 近月結算日：2026-03-25，共 242 個合約（Call 121 + Put 121）
- 符號格式：`TX429400C6`（C=買權）、`TX429400O6`（O=賣權，注意是 O 不是 P）
- 履約價從名稱欄位取（name 含「買權」/「賣權」分辨 Call/Put）
- **需要 2 條 WebSocket 連線**（242 > 200）

### Step 1.3：WebSocket 訂閱測試 ✅
- 認證、訂閱成功
- 訊息格式是**原始 JSON 字串**，需 `json.loads()` 解析
- `total.tradeVolume` / `totalBidMatch` / `totalAskMatch` 欄位確認存在
- `avgPrice`：只在交易時段有成交時才出現，盤後為空（待交易時段驗證）
- SDK 在程式結束時有 Segfault，是 SDK 本身 bug，不影響功能

### Step 1.4：計算驗算 ⏳ 待交易時段執行
- 需在 08:45~13:45 執行，確認 avgPrice 出現並驗算淨CALL/PUT 數值

---

## Phase 2 — 後端架構 🔨 進行中

### Step 2.1：`config.py`
```python
ID        = "your_id"
PASSWORD  = "your_password"
CERT_PATH = "/path/to/cert.pfx"
CERT_PASS = "your_cert_password"
```

### Step 2.2：`calculator.py`
負責所有純計算邏輯，不依賴任何 SDK：
```
parse_symbol(symbol)
  → 回傳 { strike: int, type: "C"/"P" }

calc_net_position(bid_match, ask_match, volume)
  → 回傳 淨CALL 或 淨PUT 值

calc_combined_pnl(call_data, put_data)
  → 回傳 { x: [履約價列表], y: [合併損益列表], max_pain: int }
  → call_data / put_data 各為 list of { strike, net_position, avg_price }
```

### Step 2.3：`fubon_client.py`
管理富邦 WebSocket 連線：
```
FubonClient
  ├── connect()          登入 + init_realtime + 取得合約列表 + 訂閱 WebSocket
  ├── on_message(data)   收到推播 → 更新內存 store → 觸發重算 → 呼叫 callback
  ├── reconnect()        斷線時自動重連（指數退避）
  └── store              dict，key=symbol，value=最新資料
```

### Step 2.4：`main.py`
FastAPI 主程式：
```
GET  /              → 回傳 index.html
GET  /api/data      → 回傳目前最新計算結果（JSON，供初始載入用）
GET  /api/status    → 回傳連線狀態、訂閱數、最後更新時間
WS   /ws            → 瀏覽器連線後，每次有更新就廣播最新資料
```

廣播邏輯：
- 富邦推播 → 重算 → 廣播給所有已連線的瀏覽器 WebSocket
- 多個瀏覽器同時看：只做一次計算，廣播給全部人

---

## Phase 3 — 前端 UI ✅ 完成

### Step 3.1：`index.html` 版面骨架
```
┌─────────────────────────────────────────────────────┐
│  頂部工具列：到期日 | 資料時間 | 結算日             │
├─────────────────────────────┬───────────────────────┤
│  左側：T字報價表            │  右側：損益曲線圖     │
│                             │                       │
│  [淨CALL面積] [履約價] [淨PUT面積]  │  ECharts 折線圖      │
│  （每列一個履約價）         │  Max Pain 標注        │
│  高亮目前指數附近           │  目前指數垂直線       │
│                             │                       │
└─────────────────────────────┴─────────────────────┬─┘
                                          狀態監控角落 │
                                  ● 連線：已連線      │
                                  ● 訂閱：xx 個       │
                                  ● 更新：xx:xx:xx    │
                                                      └─
```

### Step 3.2：左側 T字報價表
- 用 HTML `<table>` 實作（每列 = 一個履約價）
- 排列：**高履約價在上，低履約價在下**（與範例圖一致）
- 淨CALL 欄：數值 + 以 `<div>` 寬度比例模擬面積延伸（向左）
- 淨PUT 欄：同上（向右延伸）
- 比例計算：`width% = abs(value) / max_abs_value * 100`
- 高亮列：目前指數最近的履約價列（黃色或藍色背景）
- 正值用綠色，負值用紅色

### Step 3.3：右側損益曲線圖（ECharts）
- 折線圖（藍色曲線）
- 曲線下方填色（淡粉紅 area）
- 兩條垂直標注線：
  - Max Pain 位置（橘色虛線 + 標籤）
  - 目前指數位置（藍色實線 + 標籤）
- Y 軸：億元，顯示到小數點後兩位
- X 軸：履約價

### Step 3.4：`app.js` WebSocket 邏輯
```javascript
// 連線後端 WebSocket
const ws = new WebSocket("ws://localhost:8000/ws")

ws.onmessage = (event) => {
  const data = JSON.parse(event.data)
  updateTable(data.strikes)   // 更新左側表格
  updateChart(data.pnl)       // 更新右側圖表
  updateStatus(data.status)   // 更新狀態角落
}

// 斷線自動重連
ws.onclose = () => setTimeout(connect, 3000)
```

---

## Phase 4 — 整合測試 ⏳ 待交易時段執行

### Step 4.1：數值驗算
- 開 Excel 和網頁並排
- 確認同一時間點的淨CALL/PUT、Max Pain 點數值一致

### Step 4.2：多瀏覽器測試
- 開 3 個瀏覽器分頁同時連線
- 確認都收到即時更新
- 確認後端只跑一份計算

### Step 4.3：穩定性測試
- 讓程式跑 1 小時，觀察記憶體是否洩漏
- 手動斷網後恢復，確認自動重連正常

---

## 已知風險與待確認事項

| 風險 | 說明 | 解法 |
|------|------|------|
| WebSocket trades channel 欄位 | 未確認是否包含 avgPrice、totalBidMatch/AskMatch | Phase 1 Step 1.3 驗證，不足則補 HTTP polling |
| 近月合約數量 | 若超過 200 個需開第 2 條 WebSocket 連線 | Phase 1 Step 1.2 確認數量 |
| 同帳號多裝置登入限制 | 官方文件未說明上限 | 實測，或致電客服 0800-073588 |
| 盤後/夜盤資料 | 是否需要支援 afterhours session | MVP 先做一般交易時段 |

---

## MVP 範圍（現階段目標）

- [x] 近月 TXO 資料
- [x] 淨CALL / 淨PUT 面積圖（左側）
- [x] 合併損益曲線 + Max Pain（右側）
- [x] 即時更新（WebSocket 推播）
- [x] 狀態監控角落
- [ ] 切換不同結算日（未來）
- [ ] 切換累計區間（未來）
- [ ] 左側柱狀圖（功能待確認）
- [ ] 雲端部署（未來）
