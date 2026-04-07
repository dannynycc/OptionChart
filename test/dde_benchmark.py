"""
test/dde_benchmark.py  — v4 (persistent-thread design)

目標：只量 XQFAP DDE server 的純請求處理速度。

設計原則：
  1. 持久 thread 池：N 條 DDEML 連線在測試開始前一次性建立，整個 benchmark 期間不重建
     → 排除 connection setup 時間干擾
  2. DDEML thread affinity：每條連線只由建立它的 thread 使用，不跨 thread
  3. 每輪測試：用 Event/Barrier 協調，所有 thread 同時開始，測量從 go-signal 到最後一條 thread
     完成的 wall-clock 時間
  4. 252 symbols（匹配生產量）
  5. Warm-up round：第 0 輪不計入統計，讓 XQFAP warm up

執行：cd test && python dde_benchmark.py
"""

import sys
import os
import ctypes
import threading
import time
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import win32ui  # noqa
import dde

# ── DDEML 常數 ────────────────────────────────────────────────

_user32            = ctypes.WinDLL("user32")
_PFNCALLBACK       = ctypes.WINFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
)
_DWORD             = ctypes.c_ulong
_CF_TEXT           = 1
_XTYP_REQUEST      = 0x20B0
_DDE_TIMEOUT_MS    = 3000
_CP_WINUNICODE     = 1200
_APPCMD_CLIENTONLY = 0x0010

_user32.DdeCreateStringHandleW.restype  = ctypes.c_void_p
_user32.DdeCreateStringHandleW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_int]
_user32.DdeFreeStringHandle.argtypes    = [ctypes.c_ulong, ctypes.c_void_p]
_user32.DdeConnect.restype              = ctypes.c_void_p
_user32.DdeConnect.argtypes             = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_user32.DdeClientTransaction.restype    = ctypes.c_void_p
_user32.DdeClientTransaction.argtypes   = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
]
_user32.DdeGetData.argtypes             = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong]
_user32.DdeFreeDataHandle.argtypes      = [ctypes.c_void_p]
_user32.DdeDisconnect.argtypes          = [ctypes.c_void_p]
_user32.DdeUninitialize.argtypes        = [ctypes.c_ulong]

_keep_callbacks: list = []
_keep_lock = threading.Lock()

def _null_cb(a, b, c, d, e, f, g, h):
    return None

# ── DDEML 低層 ───────────────────────────────────────────────

def _connect() -> tuple:
    """在當前 thread 建立 DDEML 連線，回傳 (inst, hconv) 或 (None, None)"""
    inst = _DWORD(0)
    cb   = _PFNCALLBACK(_null_cb)
    with _keep_lock:
        _keep_callbacks.append(cb)   # 防 GC
    if _user32.DdeInitializeW(ctypes.byref(inst), cb, _APPCMD_CLIENTONLY, 0) != 0:
        return None, None
    hsvc   = _user32.DdeCreateStringHandleW(inst.value, "XQFAP", _CP_WINUNICODE)
    htopic = _user32.DdeCreateStringHandleW(inst.value, "Quote", _CP_WINUNICODE)
    hconv  = _user32.DdeConnect(inst.value, hsvc, htopic, None)
    _user32.DdeFreeStringHandle(inst.value, hsvc)
    _user32.DdeFreeStringHandle(inst.value, htopic)
    if not hconv:
        _user32.DdeUninitialize(inst.value)
        return None, None
    return inst, hconv


def _req(inst, hconv, item: str) -> bool:
    """發出一次 DDE request，回傳是否取得有效值（True/False）"""
    hsz   = _user32.DdeCreateStringHandleW(inst.value, item, _CP_WINUNICODE)
    dr    = _DWORD(0)
    hdata = _user32.DdeClientTransaction(
        None, 0, hconv, hsz, _CF_TEXT, _XTYP_REQUEST, _DDE_TIMEOUT_MS, ctypes.byref(dr)
    )
    _user32.DdeFreeStringHandle(inst.value, hsz)
    if not hdata:
        return False
    sz  = _user32.DdeGetData(hdata, None, 0, 0)
    buf = ctypes.create_string_buffer(max(sz, 1))
    _user32.DdeGetData(hdata, buf, sz, 0)
    _user32.DdeFreeDataHandle(hdata)
    val = buf.raw.rstrip(b"\x00").decode("cp950", errors="replace").strip()
    return bool(val and val != "-")


def _disconnect(inst, hconv):
    try: _user32.DdeDisconnect(hconv)
    except Exception: pass
    try: _user32.DdeUninitialize(inst.value)
    except Exception: pass


# ── pywin32 探索 symbols ─────────────────────────────────────

def find_symbols(n: int = 252) -> list:
    print(f"使用 pywin32 探索最多 {n} 個有效合約...")
    srv  = dde.CreateServer()
    srv.Create("BenchProbe")
    conv = dde.CreateConversation(srv)
    conv.ConnectTo("XQFAP", "Quote")

    center = 32000
    for item in ("FITX00.TF-Price", "FITXN04.TF-Price"):
        try:
            val = float(str(conv.Request(item) or "").strip())
            if val > 5000:
                center = int(round(val / 50) * 50)
                break
        except Exception:
            pass
    print(f"  台指現價中心：{center}")

    symbols = []
    for prefix in ("TXUN04", "TX2N04", "TXON04", "TXU04", "TX204", "TXON04"):
        for strike in range(center - 2000, center + 2050, 50):
            for side in ("C", "P"):
                sym = f"{prefix}{side}{strike}"
                try:
                    val = str(conv.Request(f"{sym}.TF-TotalVolume") or "").strip()
                    if val and val != "-":
                        symbols.append(sym)
                        if len(symbols) >= n:
                            print(f"  找到 {len(symbols)} 個 symbols")
                            return symbols
                except Exception:
                    pass
    print(f"  找到 {len(symbols)} 個 symbols")
    return symbols


# ── 持久 thread worker ───────────────────────────────────────

class PersistentWorker:
    """
    持久 thread：連線建立一次，透過 Event 信號反覆執行測試輪次。
    每輪收到 go 信號後處理 chunk，記錄每個 symbol 的時間，結束後 set done。
    """
    def __init__(self, idx: int, probe_item: str):
        self.idx        = idx
        self.probe_item = probe_item
        self.chunk:     list  = []
        self.fields:    list  = []
        self.result:    list  = []   # [(elapsed_s, n_ok), ...]
        self.ready      = threading.Event()  # worker → main：連線就緒
        self.go         = threading.Event()  # main → worker：開始這輪
        self.done       = threading.Event()  # worker → main：這輪結束
        self.quit       = threading.Event()  # main → worker：終止
        self.t_done:    float = 0.0          # 這輪結束的 perf_counter
        self._thread    = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        inst, hconv = _connect()
        if not inst:
            return  # 不 set ready → main 會 timeout
        # warm-up probe
        for _ in range(5):
            if _req(inst, hconv, self.probe_item):
                break
            time.sleep(0.2)
        self.ready.set()

        while not self.quit.is_set():
            self.go.wait()
            self.go.clear()
            if self.quit.is_set():
                break
            lats = []
            for sym in self.chunk:
                t0   = time.perf_counter()
                n_ok = sum(1 for f in self.fields if _req(inst, hconv, f"{sym}.{f}"))
                lats.append((time.perf_counter() - t0, n_ok))
            self.result   = lats
            self.t_done   = time.perf_counter()
            self.done.set()

        _disconnect(inst, hconv)

    def stop(self):
        self.quit.set()
        self.go.set()   # 喚醒以便退出
        self._thread.join(timeout=5)


# ── 主測試邏輯 ───────────────────────────────────────────────

def run_benchmark(symbols: list, thread_counts: list, repeats: int = 5,
                  fields: list = None):
    if fields is None:
        fields = ["TF-Bid", "TF-Ask", "TF-Price"]
    n_sym = len(symbols)
    n_fld = len(fields)
    probe = f"{symbols[0]}.{fields[0]}"
    max_n = max(thread_counts)

    print(f"\n建立最多 {max_n} 條持久 DDEML 連線（含 warm-up probe）...")
    workers: list = []
    for i in range(max_n):
        w = PersistentWorker(i, probe)
        workers.append(w)

    # 等所有 worker 就緒（最多 60 秒）
    deadline = time.time() + 60
    for w in workers:
        remaining = max(0.1, deadline - time.time())
        if not w.ready.wait(timeout=remaining):
            print(f"  [warn] worker-{w.idx} 連線超時，終止")
            w.stop()
    ready_workers = [w for w in workers if w.ready.is_set()]
    print(f"  就緒：{len(ready_workers)}/{max_n} 條")

    # ── 完整 warm-up：所有連線對所有 symbols × fields 各跑一輪 ──
    # 確保 XQFAP 的 lazy cache 對所有 252 symbols 都是 warm 狀態，
    # 消除測試順序造成的 cache 溫度不一致問題
    print(f"  全量 warm-up（{len(ready_workers)} 連線 × {n_sym} symbols × {n_fld} fields）...")
    t_wu = time.perf_counter()
    chunks_wu = [symbols[i::len(ready_workers)] for i in range(len(ready_workers))]
    for w, chunk in zip(ready_workers, chunks_wu):
        w.chunk  = chunk
        w.fields = fields
        w.result = []
        w.done.clear()
    for w in ready_workers:
        w.go.set()
    for w in ready_workers:
        w.done.wait(timeout=60)
    print(f"  warm-up 完成（{time.perf_counter() - t_wu:.1f}s）")

    if len(ready_workers) < min(thread_counts):
        print("就緒連線不足，終止測試")
        for w in workers:
            w.stop()
        return

    print(f"\n{'─'*76}")
    print(f"  symbols={n_sym}  fields/sym={n_fld}  total_req/round={n_sym*n_fld}  repeats={repeats}")
    print(f"  （第 0 輪為 warm-up，不計入統計）")
    print(f"{'─'*76}")
    print(f"  {'N':>6}  {'req/s':>8}  {'sym/s':>8}  {'total_ms':>10}  "
          f"{'avg_ms/sym':>12}  {'p50':>8}  {'p95':>8}  hit%")
    print(f"{'─'*76}")

    # 隨機化測試順序，避免順序偏差
    import random
    shuffled_counts = thread_counts[:]
    random.shuffle(shuffled_counts)
    print(f"  測試順序（隨機）：{shuffled_counts}")

    results_by_n: dict = {}
    for n_threads in shuffled_counts:
        if n_threads > len(ready_workers):
            print(f"  {n_threads:>6}  （連線不足，跳過）")
            continue

        active = ready_workers[:n_threads]
        all_runs: list = []   # [(req_s, sym_s, total_ms, avg_ms, p50_ms, p95_ms, hit_pct)]

        for rep in range(repeats + 1):   # rep=0 為 warm-up
            chunks = [symbols[i::n_threads] for i in range(n_threads)]
            for i, w in enumerate(active):
                w.chunk  = chunks[i]
                w.fields = fields
                w.result = []
                w.done.clear()

            # ── 同時發出 go 信號，記錄發出時間 ──
            t_go = time.perf_counter()
            for w in active:
                w.go.set()

            # 等全部完成
            for w in active:
                w.done.wait(timeout=30)

            # elapsed = 最晚完成的 worker 的結束時間 - t_go
            t_end   = max(w.t_done for w in active)
            elapsed = t_end - t_go

            if rep == 0:
                continue   # warm-up 不計

            # 收集 latency 數據
            all_lat: list = []
            total_ok      = 0
            for w in active:
                for lat, n_ok in w.result:
                    all_lat.append(lat)
                    total_ok += n_ok

            if not all_lat:
                continue

            all_lat.sort()
            total_req = len(all_lat) * n_fld
            hit_pct   = total_ok / total_req * 100 if total_req else 0
            req_s     = total_ok / elapsed
            sym_s     = len(all_lat) / elapsed
            avg_ms    = statistics.mean(all_lat) * 1000
            p50_ms    = all_lat[len(all_lat) // 2] * 1000
            p95_ms    = all_lat[int(len(all_lat) * 0.95)] * 1000
            all_runs.append((req_s, sym_s, elapsed * 1000, avg_ms, p50_ms, p95_ms, hit_pct))

        if not all_runs:
            results_by_n[n_threads] = None
            continue
        results_by_n[n_threads] = all_runs

    # ── 依 N 升序印出結果 ──
    print(f"\n{'─'*76}")
    print(f"  最終結果（按 N 升序，每格為 {repeats} 次的中位數）")
    print(f"{'─'*76}")
    print(f"  {'N':>6}  {'req/s':>8}  {'sym/s':>8}  {'total_ms':>10}  "
          f"{'avg_ms/sym':>12}  {'p50':>8}  {'p95':>8}  hit%  [min ~ max]")
    print(f"{'─'*76}")
    for n_threads in thread_counts:
        all_runs = results_by_n.get(n_threads)
        if not all_runs:
            print(f"  {n_threads:>6}  （無資料）")
            continue
        all_runs.sort(key=lambda x: x[2])
        mid = all_runs[len(all_runs) // 2]
        mn  = min(r[2] for r in all_runs)
        mx  = max(r[2] for r in all_runs)
        print(f"  {n_threads:>6}  {mid[0]:>8.0f}  {mid[1]:>8.0f}  "
              f"{mid[2]:>10.0f}  {mid[3]:>12.2f}  {mid[4]:>8.2f}  {mid[5]:>8.2f}  "
              f"{mid[6]:.0f}%  [{mn:.0f}~{mx:.0f}]")
    print(f"{'─'*76}")

    for w in workers:
        w.stop()
    print("  所有 worker 已終止")


# ── 入口 ─────────────────────────────────────────────────────

if __name__ == "__main__":
    symbols = find_symbols(n=252)
    if len(symbols) < 10:
        print(f"有效 symbols 太少（{len(symbols)} 個），請確認新富邦e01已開啟")
        sys.exit(1)

    print(f"\n實際測試 symbols：{len(symbols)} 個（{symbols[0]} … {symbols[-1]}）")

    run_benchmark(
        symbols       = symbols,
        thread_counts = [1, 2, 4, 8, 12, 16, 24],
        repeats       = 10,
        fields        = ["TF-Bid", "TF-Ask", "TF-Price"],
    )
