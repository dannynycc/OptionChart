"""
probe_advise4.py — 使用 PyWinDDE.py 方式測試 daqFAP advise
關鍵差異：
  1. TIMEOUT_ASYNC（非同步）而非固定逾時
  2. callback 不過濾 wType，全部印出
  3. 直接用 PyWinDDE.py 的常數定義
"""
import time
import sys
from ctypes import POINTER, WINFUNCTYPE, c_char_p, c_void_p, c_int, c_ulong, byref, create_string_buffer
from ctypes.wintypes import BOOL, DWORD, BYTE, HWND, MSG, UINT

import win32ui  # noqa

# ── 常數（來自 PyWinDDE.py，用舊版定義）──────────────────────

HCONV    = c_void_p
HDDEDATA = c_void_p
HSZ      = c_void_p
LPBYTE   = c_char_p
LPDWORD  = POINTER(DWORD)
LPCWSTR  = c_void_p

DMLERR_NO_ERROR  = 0
CF_TEXT          = 1
DDE_FACK         = 0x8000
DDE_FNOTPROCESSED = 0x0000

XTYPF_NOBLOCK    = 0x0002
XTYPF_NODATA     = 0x0004
XTYPF_ACKREQ     = 0x0008
XCLASS_BOOL      = 0x1000
XCLASS_DATA      = 0x2000
XCLASS_FLAGS     = 0x4000
XCLASS_NOTIFICATION = 0x8000

XTYP_ADVDATA     = (0x0010 | XCLASS_FLAGS)                          # 0x4010
XTYP_ADVSTART    = (0x0030 | XCLASS_BOOL)                           # 0x1030
XTYP_ADVSTOP     = (0x0040 | XCLASS_NOTIFICATION)                   # 0x8040
XTYP_REQUEST     = (0x00B0 | XCLASS_DATA)                           # 0x20B0
XTYP_XACT_COMPLETE = (0x0080 | XCLASS_NOTIFICATION)                 # 0x8080

TIMEOUT_ASYNC    = 0xFFFFFFFF
APPCMD_CLIENTONLY = 0x0010

# ── 載入 DDE 函式 ───────────────────────────────────────────

from ctypes import windll
u32 = windll.user32

DDECALLBACK = WINFUNCTYPE(HDDEDATA, UINT, UINT, HCONV, HSZ, HSZ, HDDEDATA, c_ulong, c_ulong)

u32.DdeInitializeW.argtypes      = [LPDWORD, DDECALLBACK, DWORD, DWORD]
u32.DdeInitializeW.restype       = UINT
u32.DdeCreateStringHandleW.argtypes = [DWORD, c_void_p, UINT]
u32.DdeCreateStringHandleW.restype  = HSZ
u32.DdeFreeStringHandle.argtypes = [DWORD, HSZ]
u32.DdeConnect.argtypes          = [DWORD, HSZ, HSZ, c_void_p]
u32.DdeConnect.restype           = HCONV
u32.DdeClientTransaction.argtypes = [LPBYTE, DWORD, HCONV, HSZ, UINT, UINT, DWORD, LPDWORD]
u32.DdeClientTransaction.restype  = HDDEDATA
u32.DdeAccessData.argtypes       = [HDDEDATA, LPDWORD]
u32.DdeAccessData.restype        = LPBYTE
u32.DdeUnaccessData.argtypes     = [HDDEDATA]
u32.DdeFreeDataHandle.argtypes   = [HDDEDATA]
u32.DdeQueryStringW.argtypes     = [DWORD, HSZ, c_char_p, DWORD, c_int]
u32.DdeQueryStringW.restype      = DWORD
u32.DdeGetLastError.argtypes     = [DWORD]
u32.DdeGetLastError.restype      = UINT
u32.DdeDisconnect.argtypes       = [HCONV]
u32.DdeUninitialize.argtypes     = [DWORD]
u32.GetMessageW.argtypes         = [POINTER(MSG), HWND, UINT, UINT]
u32.GetMessageW.restype          = BOOL
u32.TranslateMessage.argtypes    = [POINTER(MSG)]
u32.DispatchMessageW.argtypes    = [POINTER(MSG)]

# ── 全域狀態 ────────────────────────────────────────────────

idInst   = DWORD(0)
hConv    = None
received = {}
all_callbacks = []

TYPE_NAMES = {
    XTYP_ADVDATA:       'ADVDATA',
    XTYP_ADVSTART:      'ADVSTART',
    XTYP_ADVSTOP:       'ADVSTOP',
    XTYP_XACT_COMPLETE: 'XACT_COMPLETE',
}

def dde_callback(wType, uFmt, hconv, hsz1, hsz2, hdata, dw1, dw2):
    """不過濾 wType，全部記錄（仿照 PyWinDDE.py 原始做法）"""
    tname = TYPE_NAMES.get(wType, f'{wType:#06x}')
    ts    = time.strftime('%H:%M:%S')

    # 嘗試讀 hsz2（item name）
    item = ''
    if hsz2:
        buf = create_string_buffer(256)
        u32.DdeQueryStringW(idInst, hsz2, buf, 256, 1004)  # 1004 = CP_WINANSI
        item = buf.value.decode('cp950', errors='replace').strip()

    # 嘗試讀資料
    val = ''
    if hdata:
        pdwSize = DWORD(0)
        pData = u32.DdeAccessData(hdata, byref(pdwSize))
        if pData:
            raw = pData.split(b'\x01')[0]  # 截掉垃圾字元
            val = raw.decode('cp950', errors='replace').strip()
            u32.DdeUnaccessData(hdata)

    entry = f"[{ts}] {tname}  item={item!r}  val={val!r}"
    all_callbacks.append(entry)
    print(f"  {entry}")

    if wType == XTYP_ADVDATA and item and val:
        if item not in received:
            received[item] = []
        received[item].append(val)
        return DDE_FACK

    return 0

cb = DDECALLBACK(dde_callback)

# ── 初始化 ──────────────────────────────────────────────────

print("=== DDEML 初始化 ===")
res = u32.DdeInitializeW(byref(idInst), cb, APPCMD_CLIENTONLY, 0)
if res != DMLERR_NO_ERROR:
    print(f"DdeInitializeW 失敗 res={res}"); sys.exit(1)
print(f"  OK  idInst={idInst.value}")

hszSvc   = u32.DdeCreateStringHandleW(idInst, "XQFAP",  1200)
hszTopic = u32.DdeCreateStringHandleW(idInst, "Quote",  1200)
hConv    = u32.DdeConnect(idInst, hszSvc, hszTopic, None)
u32.DdeFreeStringHandle(idInst, hszSvc)
u32.DdeFreeStringHandle(idInst, hszTopic)
if not hConv:
    print(f"  DdeConnect 失敗 err={u32.DdeGetLastError(idInst):#06x}"); sys.exit(1)
print("  DdeConnect OK")

# ── 先做一次 REQUEST 確認連線 ──────────────────────────────

test_item = "FITX00.TF-Price"
hszItem  = u32.DdeCreateStringHandleW(idInst, test_item, 1200)
hdata    = u32.DdeClientTransaction(LPBYTE(), 0, hConv, hszItem, CF_TEXT, XTYP_REQUEST, 3000, LPDWORD())
u32.DdeFreeStringHandle(idInst, hszItem)
if hdata:
    pdwSize = DWORD(0)
    pData = u32.DdeAccessData(hdata, byref(pdwSize))
    if pData:
        val = pData.decode('cp950', errors='replace').strip()
        u32.DdeUnaccessData(hdata)
    u32.DdeFreeDataHandle(hdata)
    print(f"  REQUEST {test_item} = {val!r}".encode('cp950','replace').decode('cp950'))
else:
    print(f"  REQUEST 失敗 err={u32.DdeGetLastError(idInst):#06x}")

# ── 訂閱 advise（TIMEOUT_ASYNC）──────────────────────────────

# 用 pywin32 掃 20 個合約
import win32ui, dde as _dde
_srv  = _dde.CreateServer(); _srv.Create("ProbeAdv4")
_conv = _dde.CreateConversation(_srv); _conv.ConnectTo("XQFAP", "Quote")
_raw  = _conv.Request("FITX00.TF-Price")
_ctr  = round(float(str(_raw).strip()) / 50) * 50
print(f"\n中心價={_ctr}")
_syms = []
for _d in range(0, 11*50, 50):
    for _s in ([0] if _d == 0 else [1, -1]):
        _k = _ctr + _s * _d
        for _cp in ["C", "P"]:
            if len(_syms) >= 20: break
            _sym = f"TXYN03{_cp}{_k}"
            try:
                _n = _conv.Request(f"{_sym}.TF-Name")
                if _n and str(_n).strip() not in ('','-'): _syms.append(_sym)
            except: pass
        if len(_syms) >= 20: break
    if len(_syms) >= 20: break
try: _conv.Disconnect()
except: pass
print(f"掃到 {len(_syms)} 個合約")

ADVISE_ITEMS = ["FITX00.TF-Price"]
for _sym in _syms:
    for _f in ["TF-TotalVolume","TF-InOutRatio","TF-AvgPrice"]:
        ADVISE_ITEMS.append(f"{_sym}.{_f}")

print(f"\n=== 訂閱 advise（TIMEOUT_ASYNC，{len(ADVISE_ITEMS)} 個）===")
for item_str in ADVISE_ITEMS:
    hszItem = u32.DdeCreateStringHandleW(idInst, item_str, 1200)
    hdata   = u32.DdeClientTransaction(LPBYTE(), 0, hConv, hszItem, CF_TEXT, XTYP_ADVSTART, TIMEOUT_ASYNC, LPDWORD())
    u32.DdeFreeStringHandle(idInst, hszItem)
    if hdata:
        u32.DdeFreeDataHandle(hdata)
        print(f"  ADVSTART {item_str}: OK（async handle 回傳）")
    else:
        err = u32.DdeGetLastError(idInst)
        print(f"  ADVSTART {item_str}: FAIL  err={err:#06x}")

# ── message loop（GetMessage，非 Peek）─────────────────────

WAIT = 30
print(f"\n=== GetMessage loop {WAIT} 秒 ===")
print("（任何 callback 都會印出來）\n")

msg      = MSG()
deadline = time.time() + WAIT

import threading
def stop_loop():
    time.sleep(WAIT)
    # 發一個 WM_QUIT 讓 GetMessage 返回
    import ctypes
    ctypes.windll.user32.PostQuitMessage(0)

threading.Thread(target=stop_loop, daemon=True).start()

while u32.GetMessageW(byref(msg), HWND(), 0, 0) > 0:
    u32.TranslateMessage(byref(msg))
    u32.DispatchMessageW(byref(msg))

# ── 結果 ──────────────────────────────────────────────────

total = sum(len(v) for v in received.values())
print(f"\n=== 結果：{len(all_callbacks)} 個 callback，其中 {total} 筆 ADVDATA ===")
for item, vals in received.items():
    print(f"  {item}: {len(vals)} 筆，最新={vals[-1]!r}")

if not received:
    print("  沒有 ADVDATA，但有其他 callback：")
    for e in all_callbacks[:10]:
        print(f"    {e}")

u32.DdeDisconnect(hConv)
u32.DdeUninitialize(idInst)
