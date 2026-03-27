"""
probe_advise2.py — 乾淨的 DDEML XTYP_ADVSTART 測試
執行前必須停掉所有 xqfap_feed.py / uvicorn process

流程：
1. pywin32 讀 FITX00.TF-Price 取中心價
2. 掃描 TXY + TX4 兩個 series，找到前 5 個有效合約
3. 對這些合約訂閱 TotalVolume + InOutRatio + AvgPrice 的 advise
4. message pump 跑 20 秒，看有沒有 XTYP_ADVDATA callback
"""
import ctypes
import ctypes.wintypes as wt
import time
import sys

import win32ui  # noqa
import dde

# ── DDEML 設定 ──────────────────────────────────────────────────

user32 = ctypes.WinDLL("user32")

CF_TEXT           = 1
XTYP_ADVDATA      = 0x4010
XTYP_ADVSTART     = 0x1070
DDE_FACK          = 0x8000
APPCMD_CLIENTONLY = 0x0010
CP_WINUNICODE     = 1200
TIMEOUT_MS        = 3000
PM_REMOVE         = 1

PFNCALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
)

user32.DdeInitializeW.argtypes      = [ctypes.POINTER(ctypes.c_ulong), PFNCALLBACK, ctypes.c_ulong, ctypes.c_ulong]
user32.DdeInitializeW.restype       = ctypes.c_uint
user32.DdeCreateStringHandleW.restype  = ctypes.c_void_p
user32.DdeCreateStringHandleW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_int]
user32.DdeFreeStringHandle.argtypes    = [ctypes.c_ulong, ctypes.c_void_p]
user32.DdeConnect.restype           = ctypes.c_void_p
user32.DdeConnect.argtypes          = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
user32.DdeClientTransaction.restype = ctypes.c_void_p
user32.DdeClientTransaction.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
]
user32.DdeGetData.argtypes          = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong]
user32.DdeQueryStringW.restype      = ctypes.c_ulong
user32.DdeQueryStringW.argtypes     = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_int]
user32.DdeGetLastError.argtypes     = [ctypes.c_ulong]
user32.DdeGetLastError.restype      = ctypes.c_uint
user32.DdeDisconnect.argtypes       = [ctypes.c_void_p]
user32.DdeUninitialize.argtypes     = [ctypes.c_ulong]
user32.PeekMessageW.restype         = ctypes.c_bool
user32.PeekMessageW.argtypes        = [ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
user32.TranslateMessage.argtypes    = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes    = [ctypes.POINTER(wt.MSG)]

# ── Global state ────────────────────────────────────────────────

inst     = ctypes.c_ulong(0)
received = {}   # item_str -> [(timestamp, value), ...]

def dde_callback(uType, uFmt, hconv, hsz1, hsz2, hdata, dw1, dw2):
    if uType == XTYP_ADVDATA:
        item_buf = ctypes.create_unicode_buffer(512)
        user32.DdeQueryStringW(inst.value, hsz2, item_buf, 512, CP_WINUNICODE)
        item = item_buf.value
        val = ''
        if hdata:
            sz = user32.DdeGetData(hdata, None, 0, 0)
            if sz > 0:
                buf = ctypes.create_string_buffer(sz)
                user32.DdeGetData(hdata, buf, sz, 0)
                val = buf.raw.rstrip(b'\x00').decode('cp950', errors='replace').strip()
        if item not in received:
            received[item] = []
        received[item].append((time.time(), val))
        print(f"  [ADVDATA] {item} = {val!r}")
        return ctypes.c_void_p(DDE_FACK)
    return ctypes.c_void_p(0)

cb = PFNCALLBACK(dde_callback)

# ── Step 1: pywin32 讀中心價 ────────────────────────────────────

print("=== Step 1: 取中心價（pywin32）===")
srv  = dde.CreateServer()
srv.Create("ProbeAdvise2")
conv = dde.CreateConversation(srv)
conv.ConnectTo("XQFAP", "Quote")

raw_price = conv.Request("FITX00.TF-Price")
center    = round(float(str(raw_price).strip()) / 50) * 50
print(f"  FITX00 Price = {raw_price!r}  → center = {center}")

# ── Step 2: 找有效合約 ──────────────────────────────────────────

print("\n=== Step 2: 掃描 TXYN04 有效合約（價平上下各10檔 Call+Put）===")
STRIKE_STEP = 50
calls = []
puts  = []

# 上下各10檔 = delta 0~500，兩側
for delta in range(0, 10 * STRIKE_STEP + 1, STRIKE_STEP):
    for sign in ([1, -1] if delta > 0 else [0]):
        strike = center + sign * delta if sign != 0 else center
        for cp, lst in [("C", calls), ("P", puts)]:
            if len(lst) >= 10:
                continue
            sym  = f"TXYN04{cp}{strike}"
            try:
                name = conv.Request(f"{sym}.TF-Name")
            except Exception:
                name = None
            if name and str(name).strip() not in ('', '-'):
                lst.append(sym)
                print(f"  找到: {sym}  Name={name!r}")

candidates = calls + puts
print(f"\n  Call {len(calls)} 個，Put {len(puts)} 個，共 {len(candidates)} 個")

print(f"\n共找到 {len(candidates)} 個有效合約")

# ── Step 3: 建立 DDEML 連線 ─────────────────────────────────────

print("\n=== Step 3: DDEML 初始化 ===")
ret = user32.DdeInitializeW(ctypes.byref(inst), cb, APPCMD_CLIENTONLY, 0)
if ret != 0:
    print(f"DdeInitializeW 失敗 ret={ret}"); sys.exit(1)
print(f"  DdeInitializeW OK  inst={inst.value}")

hsz_svc   = user32.DdeCreateStringHandleW(inst.value, "XQFAP", CP_WINUNICODE)
hsz_topic = user32.DdeCreateStringHandleW(inst.value, "Quote", CP_WINUNICODE)
hconv = user32.DdeConnect(inst.value, hsz_svc, hsz_topic, None)
user32.DdeFreeStringHandle(inst.value, hsz_svc)
user32.DdeFreeStringHandle(inst.value, hsz_topic)
if not hconv:
    print("DdeConnect 失敗"); sys.exit(1)
print("  DdeConnect OK")

# ── Step 4: 訂閱 advise ─────────────────────────────────────────

FIELDS      = ["TF-TotalVolume", "TF-InOutRatio", "TF-AvgPrice"]
FORMATS     = [("CF_TEXT", CF_TEXT), ("CF_UNICODETEXT", 13), ("fmt=0", 0)]
subscribed  = []
dr          = ctypes.c_ulong(0)

print(f"\n=== Step 4: 訂閱 advise（{len(candidates)} 合約 × {len(FIELDS)} 欄位 × {len(FORMATS)} 格式）===")
for sym in candidates[:2]:   # 先只試前 2 個合約，每個試 3 種格式
    for field in FIELDS[:1]:  # 先只試 TotalVolume
        item_str = f"{sym}.{field}"
        for fmt_name, fmt_val in FORMATS:
            hsz_item = user32.DdeCreateStringHandleW(inst.value, item_str, CP_WINUNICODE)
            result   = user32.DdeClientTransaction(
                None, 0, hconv, hsz_item,
                fmt_val, XTYP_ADVSTART, TIMEOUT_MS, ctypes.byref(dr)
            )
            user32.DdeFreeStringHandle(inst.value, hsz_item)
            ok       = bool(result)
            last_err = user32.DdeGetLastError(inst.value)
            print(f"  ADVSTART {item_str} [{fmt_name}]: {'OK' if ok else f'FAIL dr={dr.value} lastErr={last_err:#06x}'}")
            if ok:
                subscribed.append((item_str, fmt_val))

# 如果某種格式成功，用那個格式訂閱剩下所有
if subscribed:
    working_fmt = subscribed[0][1]
    print(f"\n  格式 {working_fmt} 可用，訂閱剩餘合約...")
    for sym in candidates:
        for field in FIELDS:
            item_str = f"{sym}.{field}"
            if any(s == item_str for s, _ in subscribed):
                continue
            hsz_item = user32.DdeCreateStringHandleW(inst.value, item_str, CP_WINUNICODE)
            result   = user32.DdeClientTransaction(
                None, 0, hconv, hsz_item,
                working_fmt, XTYP_ADVSTART, TIMEOUT_MS, ctypes.byref(dr)
            )
            user32.DdeFreeStringHandle(inst.value, hsz_item)
            if result:
                subscribed.append((item_str, working_fmt))

print(f"\n成功訂閱：{len(subscribed)}")

# ── Step 5: message pump + 等待 advise data ─────────────────────

WAIT_SECS = 30
print(f"\n=== Step 5: message pump {WAIT_SECS} 秒，等待 ADVDATA ===")

msg      = wt.MSG()
deadline = time.time() + WAIT_SECS
last_report = time.time()

while time.time() < deadline:
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    now = time.time()
    if now - last_report >= 5:
        remaining = round(deadline - now)
        total_pts = sum(len(v) for v in received.values())
        print(f"  ... 剩 {remaining}s，收到 {len(received)} items，共 {total_pts} 筆")
        last_report = now
    time.sleep(0.001)

# ── 結果 ────────────────────────────────────────────────────────

print(f"\n=== 結果 ===")
total = sum(len(v) for v in received.values())
print(f"收到 {len(received)} 個 item，共 {total} 筆 advise 資料")
for item, entries in received.items():
    vals = [v for _, v in entries]
    print(f"  {item}: {len(entries)} 筆  最新={vals[-1]!r}")

if total == 0:
    print("\n→ 沒有收到任何資料。可能原因：")
    print("   1. daqFAP 不支援 advise（但 Excel 可以，所以不太可能）")
    print("   2. 目前非交易時間，資料沒有變動（advise 只在資料變動時推送）")
    print("   3. message pump 有問題")
else:
    print("\n→ daqFAP 支援 advise！可以改架構。")

# ── 清理 ────────────────────────────────────────────────────────

user32.DdeDisconnect(hconv)
user32.DdeUninitialize(inst.value)
