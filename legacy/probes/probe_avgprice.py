"""
probe_avgprice.py — 探測 XQFAP DDE 哪個欄位有均價小數
執行：python probe_avgprice.py
"""
import sys
import win32ui  # noqa
import dde

try:
    import config_xqfap as cfg
except ImportError:
    print("找不到 config_xqfap.py")
    sys.exit(1)

srv  = dde.CreateServer()
srv.Create("ProbeAvgPrice")
conv = dde.CreateConversation(srv)
conv.ConnectTo("XQFAP", "Quote")
print("DDE 連線成功")

# 選一個有成交量的合約來測試（用 config 裡的 series，找一個中間履約價）
series = cfg.XQ_SERIES
test_syms = [
    f"TX4{series}P33000",
    f"TX4{series}C33000",
    f"TX4{series}P32500",
    f"TX4{series}C32500",
]

# 先找一個有成交量的
sym = None
for s in test_syms:
    name = None
    try:
        v = conv.Request(f"{s}.TF-Name")
        if v and str(v).strip() not in ('', '-'):
            name = str(v).strip()
    except Exception:
        pass
    if name:
        # 確認有成交量
        try:
            vol = conv.Request(f"{s}.TF-TotalVolume")
            if vol and str(vol).strip() not in ('', '-', '0'):
                sym = s
                print(f"測試合約: {sym}  (Name={name}, TotalVolume={str(vol).strip()})")
                break
        except Exception:
            pass

if not sym:
    print("找不到有成交量的合約，請確認新富邦e01已開啟且合約已選定")
    sys.exit(1)

# 探測所有可能的均價欄位
CANDIDATES = [
    "TF-AvgPrice",
    "TF-AvgPrice1",
    "TF-AvgPrice2",
    "TF-APrice",
    "TF-Avg",
    "TF-AvgPrc",
    "TF-MeanPrice",
    "TF-VWAP",
    "TF-AvgCost",
    "TF-AvgTrade",
    "TF-TradeAvg",
    "TF-DailyAvg",
    "TF-WAP",
    "TF-WeightedAvg",
    "TF-AvgPx",
    "TF-Price",
    "TF-LastPrice",
    "TF-Last",
    "TF-Close",
    "TF-SettlePrice",
    "TF-Settlement",
    "TF-Open",
    "TF-High",
    "TF-Low",
    "TF-Bid",
    "TF-Ask",
    "TF-BestBid",
    "TF-BestAsk",
    "TF-Bid1",
    "TF-Ask1",
    # 無 TF- 前綴（嘗試 topic2 或裸名）
    "AvgPrice",
    "AvgPrc",
    "Avg",
    "Price",
    "Last",
    "Close",
]

print(f"\n{'欄位名稱':<30} {'原始值':<20} {'float值'}")
print("-" * 70)

for field in CANDIDATES:
    try:
        raw = conv.Request(f"{sym}.{field}")
        if raw is None:
            val = "(None)"
        else:
            val = str(raw).strip()
        # 嘗試轉 float
        try:
            fval = float(val) if val not in ('', '-') else 'N/A'
        except Exception:
            fval = f"(parse err: {val!r})"

        # 只顯示非空、非錯誤的
        if val not in ('', '-') and 'ERROR' not in val and 'error' not in val.lower():
            marker = " *** HAS DECIMAL ***" if isinstance(fval, float) and fval != int(fval) else ""
            print(f"{field:<30} {val!r:<20} {fval}{marker}")
        else:
            print(f"{field:<30} {val!r}")
    except Exception as e:
        print(f"{field:<30} (exception: {e})")
