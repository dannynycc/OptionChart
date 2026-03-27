"""
probe_advise.py — 測試 DDE Advise link 能否拿到小數均價
如果 advise 模式下 XQFAP push 的 AvgPrice 有小數，我們就改用 advise 架構。

執行：python probe_advise.py
"""
import sys
import time
import win32ui
import win32con
import win32gui
import dde

try:
    import config_xqfap as cfg
except ImportError:
    print("找不到 config_xqfap.py"); sys.exit(1)

series = cfg.XQ_SERIES
# 選一個有成交量的合約，P32500 的 Excel 期望值是 93.3，TF-AvgPrice 給 93
TEST_SYM  = f"TX4{series}P32500"
TEST_ITEM = f"{TEST_SYM}.TF-Name,Bid,Ask,Price,TotalVolume,InOutRatio,AvgPrice"

print(f"測試合約 : {TEST_SYM}")
print(f"測試 Item: {TEST_ITEM}")
print("（目前 Request 模式 AvgPrice = '93.' ，期望 DDE Advise 能給 '93.3'）")
print()

# ── 建立 DDE Server + 連線 ─────────────────────────────────────────────────

received_data = {}   # item → list of raw values received via advise

class MyDDEServer(dde.Server):
    """覆寫 OnAdvise，捕捉 XQFAP push 來的資料"""
    def OnAdvise(self, topic, item, data):
        key = f"{topic}|{item}"
        if key not in received_data:
            received_data[key] = []
        received_data[key].append(repr(data))
        # 立即印出
        print(f"[OnAdvise] topic={topic!r}  item={item!r}")
        print(f"           data repr = {repr(data)}")
        print()

srv = MyDDEServer()
srv.Create("ProbeAdvise")

conv = dde.CreateConversation(srv)
conv.ConnectTo("XQFAP", "Quote")
print("DDE Request 確認（對照用）：")
print(f"  TF-AvgPrice = {conv.Request(f'{TEST_SYM}.TF-AvgPrice')!r}")
print(f"  multi-field = {conv.Request(TEST_ITEM)!r}")
print()

# ── 建立 Advise link ───────────────────────────────────────────────────────

print("嘗試建立 Advise link...")

# 方法1：conv.Advise(item)
try:
    result = conv.Advise(TEST_ITEM)
    print(f"  conv.Advise(multi-field): 成功，result={result!r}")
    advise_ok = True
except AttributeError:
    print("  conv.Advise 不存在（此版 pywin32 不支援）")
    advise_ok = False
except Exception as e:
    print(f"  conv.Advise 失敗：{e}")
    advise_ok = False

# 方法2：也試 TF-AvgPrice 單欄位
if advise_ok:
    try:
        conv.Advise(f"{TEST_SYM}.TF-AvgPrice")
        print(f"  conv.Advise(TF-AvgPrice): 成功")
    except Exception as e:
        print(f"  conv.Advise(TF-AvgPrice): {e}")

print()

if not advise_ok:
    print("❌ 此 pywin32 DDE 不支援 Advise，無法測試。")
    print("   結論：只能用 Request 模式，AvgPrice 會是整數截去。")
    sys.exit(0)

# ── 跑 message loop 等 push ───────────────────────────────────────────────

print("等待 XQFAP push advise 資料（最多 10 秒）...")
deadline = time.time() + 10
while time.time() < deadline:
    win32gui.PumpWaitingMessages()
    time.sleep(0.05)
    if received_data:
        break   # 收到了就停

print()
if received_data:
    print(f"=== 收到 {sum(len(v) for v in received_data.values())} 筆 advise 資料 ===")
    for key, vals in received_data.items():
        print(f"  {key}:")
        for v in vals[:5]:
            print(f"    {v}")
else:
    print("=== 10 秒內沒收到任何 advise 資料 ===")
    print("    可能原因：XQFAP 不支援 advise，或資料沒有變動（非交易時間）")

# ── 清理 ──────────────────────────────────────────────────────────────────
try:
    conv.Unadvise(TEST_ITEM)
except Exception:
    pass
