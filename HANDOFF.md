# OptionChart 選擇權籌碼面板 — 技術交接文件

## 專案架構

```
Windows 端                              WSL Ubuntu 端
─────────────────────────────────────   ──────────────────────────────────
skcom_bridge.py                         main.py (FastAPI, port 8000)
  群益 SKCOM DLL (ctypes)                 POST /api/init   合約清單（bridge 啟動時一次）
  OnNotifyQuoteLONG callback     ──→      POST /api/feed   報價更新（每 0.5s 批次）
  _poll_worker: 每 0.5s re-sub            GET  /           網頁
                                          WS   /ws         即時推播瀏覽器
                                          GET  /api/data   HTTP polling fallback
```

## 啟動 / 停止（用腳本，不要手動）

### 一鍵啟動
```
雙擊 C:\Users\Home\Desktop\OptionBridge\start.bat
```
腳本做的事：
1. 殺掉舊的 WSL uvicorn（若有）
2. 背景啟動 WSL FastAPI server，log → `/tmp/uvicorn.log`
3. 前台啟動 Windows bridge（log 顯示在終端機）

### 一鍵停止
```
雙擊 C:\Users\Home\Desktop\OptionBridge\stop.bat
```

### 正常啟動 log（bridge 這邊看到這些才算成功）
```
登入群益 (L124439558)...
登入成功
連線國內報價伺服器...
OnConnection: code=3003 (SK_SUBJECT_CONNECTION_STOCKS_READY)
載入選擇權商品資料（LoadCommodity）...
近月結算日：20260325，共 242 個合約
POST /api/init → HTTP 200
初始快照 → HTTP 200，242 筆
```

### 開瀏覽器
```
http://localhost:8000
```
正常狀態：右下角綠點「已連線」、242 個合約、數值持續更新。

---

## 重啟注意事項

- **Server 和 bridge 必須一起重啟**。只重啟 server，bridge 不會重送 `/api/init`，store 永遠空的。
- 使用 `stop.bat` 然後等 5 秒，再 `start.bat`
- 5 秒冷卻避免 `RequestStockList` 回傳 0 個商品（DLL 殘留狀態問題）

---

## 檔案位置

| 角色 | 路徑 |
|------|------|
| Windows bridge | `C:\Users\Home\Desktop\OptionBridge\skcom_bridge.py` |
| Windows 設定 | `C:\Users\Home\Desktop\OptionBridge\config_bridge.py`（不進 git，含帳密） |
| Windows DLL | `C:\Users\Home\Desktop\OptionBridge\libs\SKCOM.dll`（+ 7 個依賴 DLL） |
| 啟動腳本 | `C:\Users\Home\Desktop\OptionBridge\start.bat` / `stop.bat` |
| WSL FastAPI | `~/OptionChart/main.py` |
| WSL 計算邏輯 | `~/OptionChart/calculator.py` |
| WSL 前端 | `~/OptionChart/static/` |
| Bridge log | `C:\Users\Home\Desktop\OptionBridge\bridge.log` |
| Server log | WSL `/tmp/uvicorn.log` |
| Git repo | `~/OptionChart/` → https://github.com/dannynycc/OptionChart |

## config_bridge.py（不進 git）
```python
SKCOM_DLL   = r"C:\Users\Home\Desktop\OptionBridge\libs\SKCOM.dll"
ID          = "L124439558"
PASSWORD    = "（密碼）"
SERVER_URL  = "http://localhost:8000"
TARGET_NAME = "台選W403"   # ← 每週要改！見下方說明
```

---

## 每週換倉：TARGET_NAME 怎麼改

週選到期後要換 `config_bridge.py` 的 `TARGET_NAME`。

用 `--discover` 模式確認名稱：
```
python skcom_bridge.py --discover
```
log 裡搜尋「台選W4」，找到下一個到期的系列名，例如 `台選W404`，填入 config。

---

## 即時更新機制（關鍵，每次 debug 前必讀）

### SKCOM 的行為（夜盤特別重要）

- `OnNotifyQuoteLONG` **只在 bid/ask 報價變動時觸發**，不是每筆成交觸發
- 夜盤成交可能發生但 bid/ask 不變 → callback 不觸發 → DLL cache 不更新 → 數值凍結
- `GetStockByStockNo` 讀的是 **DLL in-memory cache**，cache 只有 callback 觸發後才更新

### 解法：強制 re-subscribe

`_poll_worker` 每 0.5 秒呼叫 `SKQuoteLib_RequestStocks`（已訂閱的合約）：
- SKCOM 收到後重新推送全量最新快照（觸發一批 `OnNotifyQuoteLONG`）
- 每次 re-sub → ~242 個 callback → 讀到最新 nTAc/nTBc/nTQty
- 批次 POST 到 server → server 比對 → 有變動才廣播 WS → 瀏覽器 flash

### 不能做的事（踩過的坑）

- ❌ 不能用 `GetStockByStockNo` 每 5 秒輪詢全部合約
  → DLL cache 在兩次 callback 間是舊值，輪詢只會把 callback 剛推的新值蓋掉，造成數值來回跳動
- ❌ `SKQuoteLib_RequestTicks` 對 TXO 合約回傳 error 3010 (`SK_SUBJECT_TICK_STOCK_NOT_FOUND`)
  → 不支援，不要再試
- ❌ `LoadCommodity` 不能刷新報價 cache，只刷新商品清單

---

## Debug 指令

```bash
# 確認 WSL server 在跑
wsl -d Ubuntu -- bash -c "ss -tlnp | grep 8000"

# 看 server 即時 log
wsl -d Ubuntu -- bash -c "tail -f /tmp/uvicorn.log"

# 目前 store 狀態（最後更新時間、合約數量）
wsl -d Ubuntu -- bash -c "curl -s http://localhost:8000/api/status"

# 看 ATM 附近的數值
wsl -d Ubuntu -- bash -c "curl -s http://localhost:8000/api/data | python3 -c \"
import json,sys,time
d=json.load(sys.stdin)
lu=d['status']['last_updated']
print('last_updated:', time.strftime('%H:%M:%S', time.localtime(lu)))
for r in d['table']:
    if r['vol_call']>500 or r['vol_put']>500:
        print(r['strike'], 'C vol='+str(r['vol_call']), 'P vol='+str(r['vol_put']))
\""

# 確認 bridge process 在跑
tasklist | grep python

# 看 bridge log 最後 50 行
python -c "
with open(r'C:\Users\Home\Desktop\OptionBridge\bridge.log','rb') as f: d=f.read()
import sys; sys.stdout.buffer.write(d[-3000:])
"

# 看 re-sub 是否有觸發
# （在 bridge log 裡找 "_do_resubscribe 完成"）
python -c "
with open(r'C:\Users\Home\Desktop\OptionBridge\bridge.log','rb') as f: d=f.read()
import sys
for line in d.split(b'\n'):
    if b'resubscrib' in line or b'_do_' in line or b'\xe9\x87\x8d\xe6\x96\xb0' in line:
        sys.stdout.buffer.write(line+b'\n')
"
```

---

## SKCOM Struct 重要欄位（SKSTOCKLONG2）

| 欄位 | 意思 | 對應 |
|------|------|------|
| `nTAc` | 內盤累計量（賣方主動成交） | `bid_match` |
| `nTBc` | 外盤累計量（買方主動成交） | `ask_match` |
| `nTQty` | 全日累計總量（含開盤競價） | `trade_volume` |
| `nClose` | 最新成交價 × 10^nDecimal | `avg_price` |
| `nDecimal` | 價格小數位數（TXO = 2，需除以 100） | — |

### 關鍵公式
```
外盤比(%) = nTBc / (nTAc + nTBc) × 100   ← 與 XQFAP 方向一致
成交總量  = nTQty
淨CALL/PUT = nTAc - nTBc
```

---

## 合約篩選（_parse_txo）

同一履約價有兩個版本同時存在，只訂閱 C6/O6（非 AM 結算）：

| 代碼格式 | 結算方式 | 要不要訂閱 |
|----------|----------|-----------|
| `TX4{strike}C6` | PM 結算 | ✅ 訂這個 |
| `TX4{strike}C6AM` | AM 結算 | ❌ 排除 |

篩選條件：
- name 完全等於 `TARGET_NAME + 'C'` 或 `TARGET_NAME + 'P'`
- code 符合 `^TX4(\d+)(C|O)6$`（正則，排除 C6AM/O6AM）

---

## 已知問題 / 待解

- `RequestStockList` 偶發回傳 0 個商品（快速重啟後）→ 暫解：等 5 秒再重啟，bridge 內有 3 次重試
- SKCOM `OnNotifyQuoteLONG` 的 `market_no` 夜盤可能不是 3（PUT 合約偶發送非 3）→ 已修正：改用 `meta_map` 判斷，不用 market_no 過濾
