# DDE Advise 研究結論（v2.28）

## 為什麼之前的 probe 都失敗

1. **TIMEOUT 用錯**：同步 timeout（3000ms）會等待 daqFAP 的 ACK，daqFAP 拒絕就失敗。
   必須用 **TIMEOUT_ASYNC = 0xFFFFFFFF**，非同步送出後直接等 callback。

2. **XTYP 常數用錯**：Windows SDK 有新舊兩版常數定義：
   - 舊版（PyWinDDE.py 用）：`XTYP_ADVSTART = 0x1030`
   - 新版（錯的）：`XTYP_ADVSTART = 0x1070`
   daqFAP 需要舊版常數 **0x1030**。

3. **callback 回傳型別**：必須回傳整數（int），不能回傳 `c_void_p(DDE_FACK)`，
   否則 "TypeError: cannot be converted to pointer"。

---

## 正確常數（用 PyWinDDE.py 的定義）

```python
XTYPF_NOBLOCK    = 0x0002   # 注意：不是 0x0008
XTYPF_NODATA     = 0x0004
XTYPF_ACKREQ     = 0x0008   # 注意：不是 0x0002
XCLASS_BOOL      = 0x1000
XCLASS_DATA      = 0x2000
XCLASS_FLAGS     = 0x4000
XCLASS_NOTIFICATION = 0x8000

XTYP_ADVDATA     = 0x0010 | XCLASS_FLAGS          # = 0x4010
XTYP_ADVSTART    = 0x0030 | XCLASS_BOOL           # = 0x1030  ← 關鍵
XTYP_ADVSTOP     = 0x0040 | XCLASS_NOTIFICATION   # = 0x8040
XTYP_XACT_COMPLETE = 0x0080 | XCLASS_NOTIFICATION # = 0x8080
XTYP_REQUEST     = 0x00B0 | XCLASS_DATA           # = 0x20B0

TIMEOUT_ASYNC    = 0xFFFFFFFF  # ADVSTART 必須用這個
```

## ADVSTART 正確用法

```python
hdata = u32.DdeClientTransaction(LPBYTE(), 0, hConv, hszItem,
        CF_TEXT, XTYP_ADVSTART, TIMEOUT_ASYNC, LPDWORD())
# 成功回傳 async handle（non-NULL），失敗回傳 NULL
if hdata:
    u32.DdeFreeDataHandle(hdata)  # 釋放 async handle
```

---

## 資料格式觀察（ADVDATA callback 收到的原始值）

| 欄位 | 原始格式 | 解析方式 |
|------|---------|---------|
| TotalVolume | `'3048.000000'` | `float()` 轉 `int` |
| InOutRatio | `'52.87%l\xc2\xB0\x01'` | `split('%')[0]` |
| FITX00 Price | `'33538.93B\x01'` | 截斷垃圾字元 |

解碼通則：`buf.rstrip(b'\x00').split(b'\x01')[0].decode('cp950').strip()`

---

## message loop 注意

- 用 `GetMessageW`（blocking），不用 `PeekMessageW`（polling）
- 結束方式：`PostQuitMessage(0)` 讓 GetMessage 返回

---

## 參考資料

- https://code.activestate.com/recipes/577654-dde-client/ — PyWinDDE.py 原始來源
- https://github.com/TeeboneTing/GetStockDataFromDDE — PyWinDDE.py 完整程式碼
- https://pykynix.blogspot.com/2013/03/ddepython3dde.html — 使用說明 + 台股 DDE 應用
