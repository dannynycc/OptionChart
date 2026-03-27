"""
probe_ddeml.py — 直接用 Windows DDEML API（ctypes）測試：
1. XTYP_REQUEST + CF_UNICODETEXT  → 是否有小數？
2. XTYP_ADVSTART（advise link）   → push 資料是否有小數？

執行：python probe_ddeml.py
"""
import sys
import ctypes
import ctypes.wintypes as wt
import time

try:
    import config_xqfap as cfg
except ImportError:
    print("找不到 config_xqfap.py"); sys.exit(1)

series = cfg.XQ_SERIES
TEST_SYM  = f"TX4{series}P32500"
MULTI_ITEM = f"{TEST_SYM}.TF-Name,Bid,Ask,Price,TotalVolume,InOutRatio,AvgPrice"
SINGLE_ITEM = f"{TEST_SYM}.TF-AvgPrice"

# ── DDEML 常數 ─────────────────────────────────────────────────────────────
CF_TEXT        = 1
CF_UNICODETEXT = 13
CF_OEMTEXT     = 7

XTYP_ADVDATA   = 0x4010
XTYP_ADVREQ    = 0x2020
XTYP_ADVSTART  = 0x1070
XTYP_ADVSTOP   = 0x0040
XTYP_REQUEST   = 0x20B0
XTYP_CONNECT   = 0x1060
XTYP_EXECUTE   = 0x4050
XTYP_DISCONNECT= 0x00C0

XTYPF_ACKREQ   = 0x0002
DDE_FACK       = 0x8000
DDE_FNOTPROCESSED = 0

APPCLASS_STANDARD  = 0x0000
APPCMD_CLIENTONLY  = 0x0010

TIMEOUT_ASYNC = 0xFFFFFFFF
DDE_TIMEOUT_5S = 5000

CP_WINANSI = 1004
CP_WINUNICODE = 1200

# ── DDEML 函式宣告 ──────────────────────────────────────────────────────────
user32 = ctypes.WinDLL("user32")

HDDEDATA = ctypes.c_void_p
HCONV    = ctypes.c_void_p
HSZ      = ctypes.c_void_p
DWORD    = ctypes.c_ulong

# Callback type
PFNCALLBACK = ctypes.WINFUNCTYPE(
    HDDEDATA,
    ctypes.c_uint,   # uType
    ctypes.c_uint,   # uFmt
    HCONV,           # hconv
    HSZ,             # hsz1
    HSZ,             # hsz2
    HDDEDATA,        # hdata
    ctypes.c_size_t, # dwData1
    ctypes.c_size_t, # dwData2
)

DdeInitializeW = user32.DdeInitializeW
DdeInitializeW.argtypes = [ctypes.POINTER(DWORD), PFNCALLBACK, DWORD, DWORD]
DdeInitializeW.restype = ctypes.c_uint

DdeUninitialize = user32.DdeUninitialize
DdeUninitialize.argtypes = [DWORD]
DdeUninitialize.restype = ctypes.c_bool

DdeCreateStringHandleW = user32.DdeCreateStringHandleW
DdeCreateStringHandleW.argtypes = [DWORD, ctypes.c_wchar_p, ctypes.c_int]
DdeCreateStringHandleW.restype = HSZ

DdeFreeStringHandle = user32.DdeFreeStringHandle
DdeFreeStringHandle.argtypes = [DWORD, HSZ]
DdeFreeStringHandle.restype = ctypes.c_bool

DdeConnect = user32.DdeConnect
DdeConnect.argtypes = [DWORD, HSZ, HSZ, ctypes.c_void_p]
DdeConnect.restype = HCONV

DdeDisconnect = user32.DdeDisconnect
DdeDisconnect.argtypes = [HCONV]
DdeDisconnect.restype = ctypes.c_bool

DdeClientTransaction = user32.DdeClientTransaction
DdeClientTransaction.argtypes = [
    ctypes.c_char_p,  # pData (NULL for request)
    DWORD,             # cbData (0 for request)
    HCONV,             # hConv
    HSZ,               # hszItem
    ctypes.c_uint,     # wFmt
    ctypes.c_uint,     # wType
    DWORD,             # dwTimeout
    ctypes.POINTER(DWORD),  # pdwResult
]
DdeClientTransaction.restype = HDDEDATA

DdeGetData = user32.DdeGetData
DdeGetData.argtypes = [HDDEDATA, ctypes.c_char_p, DWORD, DWORD]
DdeGetData.restype = DWORD

DdeFreeDataHandle = user32.DdeFreeDataHandle
DdeFreeDataHandle.argtypes = [HDDEDATA]
DdeFreeDataHandle.restype = ctypes.c_bool

DdeGetLastError = user32.DdeGetLastError
DdeGetLastError.argtypes = [DWORD]
DdeGetLastError.restype = ctypes.c_uint

# Message pump
PeekMessageW = user32.PeekMessageW
DispatchMessageW = user32.DispatchMessageW
TranslateMessage = user32.TranslateMessage

class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT),
                ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
                ("time", wt.DWORD), ("pt", wt.POINT)]

PM_REMOVE = 0x0001

# ── 全域狀態 ────────────────────────────────────────────────────────────────
advise_received = []   # (fmt_name, raw_bytes, decoded_str) tuples
idInst = DWORD(0)

def _get_data(hdata, fmt):
    """從 HDDEDATA handle 讀出 raw bytes 並解碼"""
    size = DdeGetData(hdata, None, 0, 0)
    if size == 0:
        return b"", ""
    buf = ctypes.create_string_buffer(size)
    DdeGetData(hdata, buf, size, 0)
    raw = buf.raw
    # 解碼
    if fmt == CF_UNICODETEXT:
        try:
            decoded = raw.rstrip(b"\x00").decode("utf-16-le", errors="replace")
        except Exception:
            decoded = repr(raw)
    else:
        try:
            decoded = raw.rstrip(b"\x00").decode("cp950", errors="replace")
        except Exception:
            try:
                decoded = raw.rstrip(b"\x00").decode("utf-8", errors="replace")
            except Exception:
                decoded = repr(raw)
    return raw, decoded

def dde_callback(uType, uFmt, hconv, hsz1, hsz2, hdata, dw1, dw2):
    if uType == XTYP_ADVDATA:
        fmt_name = {CF_TEXT: "CF_TEXT", CF_UNICODETEXT: "CF_UNICODETEXT",
                    CF_OEMTEXT: "CF_OEMTEXT"}.get(uFmt, f"fmt={uFmt}")
        raw, decoded = _get_data(hdata, uFmt)
        advise_received.append((fmt_name, raw, decoded))
        print(f"  [XTYP_ADVDATA] fmt={fmt_name}, decoded={decoded!r}")
        return ctypes.cast(ctypes.c_void_p(DDE_FACK), HDDEDATA)
    return ctypes.cast(ctypes.c_void_p(0), HDDEDATA)

_callback_ref = PFNCALLBACK(dde_callback)

# ── 初始化 ──────────────────────────────────────────────────────────────────
print(f"測試合約 : {TEST_SYM}")
print(f"測試 Item: {SINGLE_ITEM}")
print(f"         + {MULTI_ITEM}")
print()

ret = DdeInitializeW(ctypes.byref(idInst), _callback_ref, APPCMD_CLIENTONLY, 0)
if ret != 0:  # DMLERR_NO_ERROR = 0
    print(f"DdeInitializeW 失敗，ret={ret}"); sys.exit(1)
print(f"DDE 初始化成功，idInst={idInst.value}")

# 建立 string handle
hsz_service = DdeCreateStringHandleW(idInst.value, "XQFAP",  CP_WINUNICODE)
hsz_topic   = DdeCreateStringHandleW(idInst.value, "Quote",  CP_WINUNICODE)
hsz_single  = DdeCreateStringHandleW(idInst.value, SINGLE_ITEM,  CP_WINUNICODE)
hsz_multi   = DdeCreateStringHandleW(idInst.value, MULTI_ITEM,   CP_WINUNICODE)

# 連線
hconv = DdeConnect(idInst.value, hsz_service, hsz_topic, None)
if not hconv:
    err = DdeGetLastError(idInst.value)
    print(f"DdeConnect 失敗，error={err}"); sys.exit(1)
print("DDE 連線成功")
print()

# ── 1. XTYP_REQUEST + CF_TEXT（對照組） ─────────────────────────────────────
print("=== [1] XTYP_REQUEST CF_TEXT（對照） ===")
for hsz_item, label in [(hsz_single, "TF-AvgPrice"), (hsz_multi, "multi-field")]:
    dwResult = DWORD(0)
    hdata = DdeClientTransaction(None, 0, hconv, hsz_item, CF_TEXT, XTYP_REQUEST, DDE_TIMEOUT_5S, ctypes.byref(dwResult))
    if hdata:
        raw, decoded = _get_data(hdata, CF_TEXT)
        DdeFreeDataHandle(hdata)
        print(f"  {label}: {decoded!r}")
    else:
        err = DdeGetLastError(idInst.value)
        print(f"  {label}: 失敗 (error={err})")

# ── 2. XTYP_REQUEST + CF_UNICODETEXT ────────────────────────────────────────
print()
print("=== [2] XTYP_REQUEST CF_UNICODETEXT ===")
for hsz_item, label in [(hsz_single, "TF-AvgPrice"), (hsz_multi, "multi-field")]:
    dwResult = DWORD(0)
    hdata = DdeClientTransaction(None, 0, hconv, hsz_item, CF_UNICODETEXT, XTYP_REQUEST, DDE_TIMEOUT_5S, ctypes.byref(dwResult))
    if hdata:
        raw, decoded = _get_data(hdata, CF_UNICODETEXT)
        DdeFreeDataHandle(hdata)
        print(f"  {label}: {decoded!r}  (raw hex: {raw[:20].hex()})")
    else:
        err = DdeGetLastError(idInst.value)
        print(f"  {label}: 失敗 (error={err})")

# ── 3. XTYP_ADVSTART（advise link） ─────────────────────────────────────────
print()
print("=== [3] XTYP_ADVSTART + CF_TEXT（advise link）===")
for hsz_item, label in [(hsz_single, "TF-AvgPrice"), (hsz_multi, "multi-field")]:
    dwResult = DWORD(0)
    hdata = DdeClientTransaction(None, 0, hconv, hsz_item, CF_TEXT, XTYP_ADVSTART, DDE_TIMEOUT_5S, ctypes.byref(dwResult))
    ok = bool(hdata) or (dwResult.value != 0)
    print(f"  ADVSTART {label}: {'成功' if ok else '失敗 (error=' + str(DdeGetLastError(idInst.value)) + ')'}")

# ── 4. 跑 message loop，等 advise push ──────────────────────────────────────
print()
print("等待 XQFAP advise push（最多 8 秒）...")
msg = MSG()
deadline = time.time() + 8
while time.time() < deadline:
    while PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        TranslateMessage(ctypes.byref(msg))
        DispatchMessageW(ctypes.byref(msg))
    time.sleep(0.05)
    if advise_received:
        print(f"  收到 {len(advise_received)} 筆 advise 資料！")
        break

if not advise_received:
    print("  8 秒內無 advise 資料（XQFAP 無資料變動，或不支援 advise）")

# ── 清理 ──────────────────────────────────────────────────────────────────
DdeDisconnect(hconv)
DdeFreeStringHandle(idInst.value, hsz_service)
DdeFreeStringHandle(idInst.value, hsz_topic)
DdeFreeStringHandle(idInst.value, hsz_single)
DdeFreeStringHandle(idInst.value, hsz_multi)
DdeUninitialize(idInst.value)
print()
print("清理完成。")
