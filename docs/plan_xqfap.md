# plan: xqfap_feed.py

## 目標
用新富邦e01 DDE (XQFAP server) 取得選擇權外盤/內盤口數，
取代 capital_feed.py（群益），成為獨立報價橋接。

## 確認事項（已驗證）
- DDE server: `XQFAP`，topic: `Quote`
- Item 格式: `{symbol}.TF-{field}`
- 有效欄位: OutSize（外盤口數）, InSize（內盤口數）,
            TotalVolume（總成交量）, AvgPrice（成交均價）
- 台指期近月: `FITX00.TF-Price` → 可取當前指數作為履約價中心
- 選擇權 symbol 格式: `TX4{SERIES}C{strike}` / `TX4{SERIES}P{strike}`
  範例: TX4N03C32350（N03 = W4 of March）

## 架構
xqfap_feed.py（獨立，不依賴 fubon_feed / capital_feed）

```
新富邦e01 (XQFAP DDE)
  ↓  OutSize/InSize/TotalVolume/AvgPrice
xqfap_feed.py
  ↓  POST /api/init + /api/feed
main.py (FastAPI)
  ↓  WebSocket broadcast
瀏覽器
```

## 啟動方式
`start.bat xqfap`（現有 bat 已支援 %BROKER% 參數）

## 設定檔 config_xqfap.py
```python
XQ_SERIES       = "N03"          # 每週換倉時更新（N=W4, 03=March）
SETTLEMENT_DATE = "20260326"     # 結算日
SERVER_URL      = "http://localhost:8000"
```

## 合約探索邏輯
1. 查 FITX00.TF-Price 取得目前指數（作為 STRIKE_CENTER）
2. 探索 STRIKE_CENTER ± 3500，step 50
3. 對每個 strike 試 Call + Put
4. `Name` 欄位非 '-' 且非空 → 有效合約
5. 收集所有有效合約 → POST /api/init

## 主迴圈（每 1 秒）
1. 批次 Request OutSize/InSize/TotalVolume/AvgPrice
2. 與 prev 比較，有變動才加入 batch
3. batch 非空 → POST /api/feed
4. DDE 異常 → 重連

## 欄位對應（→ FeedItem）
| XQFAP 欄位   | FeedItem 欄位 | 說明          |
|-------------|--------------|---------------|
| OutSize     | bid_match    | 外盤口數       |
| InSize      | ask_match    | 內盤口數       |
| TotalVolume | trade_volume | 總成交量（口）  |
| AvgPrice    | avg_price    | 成交均價       |
| —           | bid_match_day = -1 | 不提供日盤分離 |

## 自動重新初始化
- 08:43（日盤前）、14:58（夜盤前）自動重探合約 + re-POST /api/init
- 換週時使用者更新 config_xqfap.py 的 XQ_SERIES + SETTLEMENT_DATE

## 新增檔案
- `xqfap_feed.py`
- `config_xqfap_template.py`
- `config_xqfap.py`（不進 git，含實際設定）

## 不改動的檔案
- main.py, calculator.py, static/* — 無需修改
- start.bat, stop.bat — 無需修改（支援 xqfap 參數）
- fubon_feed.py — 保留（用戶選擇性啟動）
- capital_feed.py — 保留（不刪，停用即可）
