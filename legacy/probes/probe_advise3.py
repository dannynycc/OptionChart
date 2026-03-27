"""
probe_advise3.py — 修正版 DDEML Advise 測試
修正點：
  1. XTYP_ADVDATA 用正確 SDK 值 0x4018（含 XTYPF_NOBLOCK）
  2. 不用 APPCMD_CLIENTONLY，改用 APPCLASS_STANDARD
  3. 試 XTYPF_NODATA（只通知不附資料）和 XTYPF_ACKREQ 組合
  4. 掃描 TXYN03（已確認有效合約）

執行前確認 xqfap_feed.py 已停止。
"""
import ctypes
import ctypes.wintypes as wt
import time
import sys

import win32ui  # noqa
import dde

user32 = ctypes.WinDLL("user32")

# ── DDEML 常數（對照 Windows SDK ddeml.h）──────────────────────
CF_TEXT          = 1
CF_UNICODETEXT   = 13

XTYPF_ACKREQ     = 0x0002
XTYPF_NOBLOCK    = 0x0008
XTYPF_NODATA     = 0x0004

XCLASS_BOOL      = 0x1000
XCLASS_DATA      = 0x2000
XCLASS_FLAGS     = 0x4000
XCLASS_NOTIF     = 0x8000

# 正確 SDK 值
XTYP_ADVDATA     = 0x0010 | XCLASS_FLAGS | XTYPF_NOBLOCK   # = 0x4018
XTYP_ADVREQ      = 0x0020 | XCLASS_DATA  | XTYPF_NOBLOCK   # = 0x2028
XTYP_ADVSTART    = 0x0070 | XCLASS_BOOL                    # = 0x1070
XTYP_ADVSTOP     = 0x0040 | XCLASS_NOTIF                   # = 0x8040
XTYP_REQUEST     = 0x00B0 | XCLASS_DATA  | XTYPF_NOBLOCK   # = 0x20B8 (for reference)

DDE_FACK         = 0x8000
DDE_FNOTPROCESSED = 0

APPCLASS_STANDARD = 0x0000   # 完整模式（非 client-only）
CP_WINUNICODE    = 1200
TIMEOUT_MS       = 3000
PM_REMOVE        = 1

# ── ctypes 函式宣告 ────────────────────────────────────────────

PFNCALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
)

user32.DdeInitializeW.argtypes       = [ctypes.POINTER(ctypes.c_ulong), PFNCALLBACK, ctypes.c_ulong, ctypes.c_ulong]
user32.DdeInitializeW.restype        = ctypes.c_uint
user32.DdeCreateStringHandleW.restype  = ctypes.c_void_p
user32.DdeCreateStringHandleW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_int]
user32.DdeFreeStringHandle.argtypes   = [ctypes.c_ulong, ctypes.c_void_p]
user32.DdeConnect.restype            = ctypes.c_void_p
user32.DdeConnect.argtypes           = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
user32.DdeClientTransaction.restype  = ctypes.c_void_p
user32.DdeClientTransaction.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
]
user32.DdeGetData.argtypes           = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong]
user32.DdeQueryStringW.restype       = ctypes.c_ulong
user32.DdeQueryStringW.argtypes      = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_int]
user32.DdeGetLastError.argtypes      = [ctypes.c_ulong]
user32.DdeGetLastError.restype       = ctypes.c_uint
user32.DdeDisconnect.argtypes        = [ctypes.c_void_p]
user32.DdeUninitialize.argtypes      = [ctypes.c_ulong]
user32.PeekMessageW.restype          = ctypes.c_bool
user32.PeekMessageW.argtypes         = [ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
user32.TranslateMessage.argtypes     = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes     = [ctypes.POINTER(wt.MSG)]

# ── Global state ───────────────────────────────────────────────

inst     = ctypes.c_ulong(0)
received = {}   # item -> [(ts, val)]

def dde_callback(uType, uFmt, hconv, hsz1, hsz2, hdata, dw1, dw2):
    # 印出所有 callback（方便 debug）
    type_names = {
        XTYP_ADVDATA: 'ADVDATA',
        XTYP_ADVREQ:  'ADVREQ',
    }
    name = type_names.get(uType, f'TYPE={uType:#06x}')

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
        print(f"  ★ [ADVDATA] {item} = {val!r}")
        return ctypes.c_void_p(DDE_FACK)

    # 其他 callback 也印出來
    print(f"  [callback] uType={name} uFmt={uFmt}")
    return ctypes.c_void_p(0)

cb = PFNCALLBACK(dde_callback)

# ── Step 1: 中心價 ─────────────────────────────────────────────

print("=== Step 1: 取中心價 ===")
srv  = dde.CreateServer()
srv.Create("ProbeAdvise3")
conv = dde.CreateConversation(srv)
conv.ConnectTo("XQFAP", "Quote")
raw  = conv.Request("FITX00.TF-Price")
center = round(float(str(raw).strip()) / 50) * 50
print(f"  Price={raw!r}  center={center}")

# ── Step 2: 掃描 TXYN03 ────────────────────────────────────────

print("\n=== Step 2: 掃描 TXYN03（上下各10檔）===")
STEP = 50
calls, puts = [], []
for delta in range(0, 11 * STEP, STEP):
    for sign in ([0] if delta == 0 else [1, -1]):
        strike = center + sign * delta
        for cp, lst in [("C", calls), ("P", puts)]:
            if len(lst) >= 10:
                continue
            sym = f"TXYN03{cp}{strike}"
            try:
                name = conv.Request(f"{sym}.TF-Name")
            except Exception:
                name = None
            if name and str(name).strip() not in ('', '-'):
                lst.append(sym)

candidates = calls + puts
print(f"  Call {len(calls)} 個，Put {len(puts)} 個，共 {len(candidates)} 個")
for s in candidates:
    print(f"    {s}")

if not candidates:
    print("  找不到合約，無法繼續。")
    sys.exit(1)

# pywin32 連線先關掉，避免與 DDEML 衝突
try:
    conv.Disconnect()
except Exception:
    pass

# ── Step 3: DDEML 初始化（APPCLASS_STANDARD）─────────────────

print("\n=== Step 3: DDEML 初始化（APPCLASS_STANDARD，非 client-only）===")
ret = user32.DdeInitializeW(ctypes.byref(inst), cb, APPCLASS_STANDARD, 0)
if ret != 0:
    print(f"  DdeInitializeW 失敗 ret={ret}"); sys.exit(1)
print(f"  OK  inst={inst.value}")

hsz_svc   = user32.DdeCreateStringHandleW(inst.value, "XQFAP", CP_WINUNICODE)
hsz_topic = user32.DdeCreateStringHandleW(inst.value, "Quote", CP_WINUNICODE)
hconv = user32.DdeConnect(inst.value, hsz_svc, hsz_topic, None)
user32.DdeFreeStringHandle(inst.value, hsz_svc)
user32.DdeFreeStringHandle(inst.value, hsz_topic)
if not hconv:
    print(f"  DdeConnect 失敗 err={user32.DdeGetLastError(inst.value):#06x}"); sys.exit(1)
print("  DdeConnect OK")

# ── Step 4: 試各種 ADVSTART 組合（先用 1 個合約）─────────────

sym0  = candidates[0]
field = "TF-TotalVolume"
item0 = f"{sym0}.{field}"

COMBOS = [
    ("ADVSTART",           XTYP_ADVSTART,                         CF_TEXT),
    ("ADVSTART+NODATA",    XTYP_ADVSTART | XTYPF_NODATA,          CF_TEXT),
    ("ADVSTART+ACKREQ",    XTYP_ADVSTART | XTYPF_ACKREQ,          CF_TEXT),
    ("ADVSTART+UNICODE",   XTYP_ADVSTART,                         CF_UNICODETEXT),
    ("ADVSTART+fmt0",      XTYP_ADVSTART,                         0),
]

print(f"\n=== Step 4: 試各種 ADVSTART 組合（{item0}）===")
working = None
dr = ctypes.c_ulong(0)

for combo_name, wtype, wfmt in COMBOS:
    hsz_item = user32.DdeCreateStringHandleW(inst.value, item0, CP_WINUNICODE)
    result   = user32.DdeClientTransaction(
        None, 0, hconv, hsz_item, wfmt, wtype, TIMEOUT_MS, ctypes.byref(dr)
    )
    user32.DdeFreeStringHandle(inst.value, hsz_item)
    err = user32.DdeGetLastError(inst.value)
    ok  = bool(result)
    print(f"  {combo_name:25s}: {'OK ← 成功！' if ok else f'FAIL  err={err:#06x}'}")
    if ok and working is None:
        working = (wtype, wfmt, combo_name)

# ── Step 5: 若有成功組合，訂閱全部合約 ───────────────────────

if working:
    wtype, wfmt, wname = working
    print(f"\n  成功組合：{wname}，訂閱剩餘 {len(candidates)-1} 個合約...")
    for sym in candidates:
        for f in ["TF-TotalVolume", "TF-InOutRatio", "TF-AvgPrice"]:
            item_str = f"{sym}.{f}"
            if item_str == item0:
                continue
            hsz_item = user32.DdeCreateStringHandleW(inst.value, item_str, CP_WINUNICODE)
            user32.DdeClientTransaction(None, 0, hconv, hsz_item, wfmt, wtype, TIMEOUT_MS, ctypes.byref(dr))
            user32.DdeFreeStringHandle(inst.value, hsz_item)
else:
    print("\n  所有組合都失敗，繼續 message pump 看有沒有其他 callback...")

# ── Step 6: message pump 30 秒 ─────────────────────────────────

WAIT = 30
print(f"\n=== Step 5: message pump {WAIT} 秒 ===")
msg      = wt.MSG()
deadline = time.time() + WAIT
last_rpt = time.time()
while time.time() < deadline:
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    if time.time() - last_rpt >= 5:
        total = sum(len(v) for v in received.values())
        print(f"  ... 剩 {round(deadline-time.time())}s，收到 {total} 筆")
        last_rpt = time.time()
    time.sleep(0.001)

# ── 結果 ──────────────────────────────────────────────────────

total = sum(len(v) for v in received.values())
print(f"\n=== 結果：收到 {len(received)} items，{total} 筆資料 ===")
for item, entries in received.items():
    print(f"  {item}: {len(entries)} 筆，最新={entries[-1][1]!r}")

# ── 清理 ──────────────────────────────────────────────────────
user32.DdeDisconnect(hconv)
user32.DdeUninitialize(inst.value)
