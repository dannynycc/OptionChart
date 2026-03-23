# OptionChart 公式文件

## Excel 欄位定義（原始設計）

### CALL 區塊
| 欄位 | 表頭名稱 | 說明 | 範例公式 |
|------|----------|------|----------|
| A欄 | 商品 | CALL 商品代碼（從報價軟體 XQFAP 拉） | `{=XQFAP\|Quote!'TX4N03C32600.TF-Name,Bid,Ask,Price,TotalVolume,InOutRatio,AvgPrice'}` |
| E欄 | 總量 | CALL 成交量（TotalVolume） | 從 A 欄報價解析 |
| F欄 | 內外盤比圖 | CALL 內外盤比（InOutRatio，0~100） | 從 A 欄報價解析 |
| G欄 | 均價 | CALL 均價（AvgPrice，即權利金） | `{=XQFAP\|Quote!'TX4N03C32550.TF-Name,Bid,Ask,Price,TotalVolume,InOutRatio,AvgPrice'}` |
| H欄 | 淨CALL | 自定義籌碼值 | `=IFERROR(ROUND((F268-50)/50*E268, 0), "")` |
| I欄 | 履約價 | 從商品代碼後5碼取出（文字） | `=RIGHT(A254, 5)` |

### PUT 區塊
| 欄位 | 表頭名稱 | 說明 | 範例公式 |
|------|----------|------|----------|
| O欄 | 總量 | PUT 成交量（TotalVolume） | 從報價軟體解析 |
| P欄 | 內外盤比圖 | PUT 內外盤比（InOutRatio，0~100） | 從報價軟體解析 |
| Q欄 | 均價 | PUT 均價（AvgPrice，即權利金） | `{=XQFAP\|Quote!'TX4N03P32550.TF-Name,Bid,Ask,Price,TotalVolume,InOutRatio,AvgPrice'}` |
| J欄 | 淨PUT | 自定義籌碼值 | `=IFERROR(ROUND((P264-50)/50*O264, 0), "")` |

### 損益計算區塊
| 欄位 | 表頭名稱 | 說明 | 範例公式 |
|------|----------|------|----------|
| AD欄 | 履約價 | 履約價（數值型） | `=I254*1` |
| AE欄 | CALL損益(億) | CALL 買方總損益 | `=Calculate_Call_ProfitLoss(AD254, $I$2:$I$399, $H$2:$H$399, $G$2:$G$399)` |
| AF欄 | PUT損益(億) | PUT 買方總損益 | `=Calculate_Put_ProfitLoss(AD251, $I$2:$I$399, $J$2:$J$399, $Q$2:$Q$399)` |
| AG欄 | 合併損益(億) | CALL + PUT 合計 | `=AE247+AF247` |

**資料範圍：** 全部欄位第 2 列～第 399 列

---

## 淨CALL / 淨PUT 公式

```
淨CALL[i] = ROUND((InOutRatio[i] - 50) / 50 * TotalVolume[i], 0)
淨PUT[i]  = ROUND((InOutRatio[i] - 50) / 50 * TotalVolume[i], 0)
```

- InOutRatio > 50 → 偏買方 → 正值
- InOutRatio < 50 → 偏賣方 → 負值
- 視覺化方式：**波浪狀面積區域**（非柱狀圖）

Python 換算（from fubon API）：
```python
inner_outer_ratio = total_bid_match / (total_bid_match + total_ask_match) * 100
net_call = round((inner_outer_ratio - 50) / 50 * trade_volume, 0)
```

---

## 損益計算公式

### PUT 損益（原始 VBA）
```vba
Function Calculate_Put_ProfitLoss(settlement_price As Range, strike_range As Range, position_range As Range, premium_range As Range) As Double
    Dim total_pnl As Double
    Dim i As Long
    Dim strike_value As Double
    Dim position_value As Double
    Dim premium_value As Double
    total_pnl = 0
    For i = 1 To strike_range.Cells.Count
        If IsNumeric(strike_range.Cells(i).Value) Then strike_value = strike_range.Cells(i).Value Else strike_value = 0
        If IsNumeric(position_range.Cells(i).Value) Then position_value = position_range.Cells(i).Value Else position_value = 0
        If IsNumeric(premium_range.Cells(i).Value) Then premium_value = premium_range.Cells(i).Value Else premium_value = 0
        Dim intrinsic_value As Double
        If strike_value > settlement_price.Value Then
            intrinsic_value = strike_value - settlement_price.Value
        Else
            intrinsic_value = 0
        End If
        Dim pnl_points As Double
        pnl_points = intrinsic_value - premium_value
        total_pnl = total_pnl + (position_value * pnl_points)
    Next i
    Calculate_Put_ProfitLoss = (total_pnl * 50) / 100000000
End Function
```

### CALL 損益（原始 VBA）
```vba
Function Calculate_Call_ProfitLoss(settlement_price As Range, strike_range As Range, position_range As Range, premium_range As Range) As Double
    Dim total_pnl As Double
    Dim i As Long
    Dim strike_value As Double
    Dim position_value As Double
    Dim premium_value As Double
    total_pnl = 0
    For i = 1 To strike_range.Cells.Count
        If IsNumeric(strike_range.Cells(i).Value) Then strike_value = strike_range.Cells(i).Value Else strike_value = 0
        If IsNumeric(position_range.Cells(i).Value) Then position_value = position_range.Cells(i).Value Else position_value = 0
        If IsNumeric(premium_range.Cells(i).Value) Then premium_value = premium_range.Cells(i).Value Else premium_value = 0
        Dim intrinsic_value As Double
        If settlement_price.Value > strike_value Then
            intrinsic_value = settlement_price.Value - strike_value
        Else
            intrinsic_value = 0
        End If
        Dim pnl_points As Double
        pnl_points = intrinsic_value - premium_value
        total_pnl = total_pnl + (position_value * pnl_points)
    Next i
    Calculate_Call_ProfitLoss = (total_pnl * 50) / 100000000
End Function
```

### Python 翻譯版
```python
def calculate_put_pnl(settlement_price, strikes, positions, premiums):
    total_pnl = 0
    for strike, position, premium in zip(strikes, positions, premiums):
        intrinsic = max(strike - settlement_price, 0)  # PUT 履約價值
        pnl_points = intrinsic - premium               # 買方淨損益（點）
        total_pnl += position * pnl_points
    return (total_pnl * 50) / 100_000_000              # 轉換為億元

def calculate_call_pnl(settlement_price, strikes, positions, premiums):
    total_pnl = 0
    for strike, position, premium in zip(strikes, positions, premiums):
        intrinsic = max(settlement_price - strike, 0)  # CALL 履約價值
        pnl_points = intrinsic - premium               # 買方淨損益（點）
        total_pnl += position * pnl_points
    return (total_pnl * 50) / 100_000_000              # 轉換為億元

# 合併損益（右側曲線）
# 對每個可能結算價 x：
combined_pnl[x] = calculate_call_pnl(x, ...) + calculate_put_pnl(x, ...)
# Max Pain = argmin(combined_pnl)  ← 曲線最低點
```

**單位換算：** 總點數 × 50（每點50元）÷ 100,000,000 = 億元

---

## 富邦 API 欄位對應

| 需要的值 | 富邦 API 欄位 |
|----------|--------------|
| 成交量 TotalVolume | `total.tradeVolume` |
| 內外盤比 InOutRatio | `total.totalBidMatch / (total.totalBidMatch + total.totalAskMatch) * 100` |
| 均價 AvgPrice（權利金） | `avgPrice` |
| 履約價 | symbol 後5碼，或 API name 欄位解析 |

### 商品代碼格式
```
TX4N03C32600  → TXO 台指選擇權，近月，Call，履約價 32600
TX4N03P32600  → TXO 台指選擇權，近月，Put，履約價 32600
```
