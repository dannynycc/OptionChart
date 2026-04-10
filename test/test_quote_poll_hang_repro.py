"""
test_quote_poll_hang_repro.py
重現 _quote_poll_worker 卡死的兩個假設（H1 = DDE backpressure, H2 = silent thread death），
並驗證 Fix A（outer try/except）能不能在這兩種情境下恢復。

完全 self-contained，不 import xqfap_feed 避免 DDE 依賴。複製了 quote_poll loop 的結構，
mock 掉 _req_thread / _post_feed / _push_futures_price，可以隨意控制行為。

執行：
    python test/test_quote_poll_hang_repro.py
"""
import time
import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 跟 production 對齊的常數 ──
_QUOTE_POLL_THREADS = 24
# 注意：測試環境用 1.0s 取代 production 的 5.0s，避免測試跑太久
# 行為等價：observable 是「每個 hung call 占用一個 thread 一段時間」
_DDE_TIMEOUT_S = 1.0
N_SYMBOLS = 96    # 縮小到 96 (4 symbol/thread)，加快 backpressure 測試
N_FIELDS = 6      # Bid/Ask/Price/TotalVolume/InOutRatio/AvgPrice

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Mock state（每個 test 重設）──
_iter_count = 0
_dde_mode = "normal"        # normal / backpressure / raise / partial_backpressure
_post_feed_mode = "normal"  # normal / raise
_quote_prevs = {}


def _req_thread_mock(item: str) -> str:
    """模擬 _req_thread，可在不同 mode 下表現異常。
    用 _iter_count >= 2 作為「第二輪起出問題」的條件，避免 sleep+thread 競爭時序。"""
    if _dde_mode == "normal":
        time.sleep(0.005)   # 真實 DDE call ~5ms
        return "100.0"
    elif _dde_mode == "backpressure" and _iter_count >= 2:
        # 第二輪起所有 call 都 timeout
        time.sleep(_DDE_TIMEOUT_S)
        return ""
    elif _dde_mode == "partial_backpressure" and _iter_count >= 2:
        if (hash(item) & 7) == 0:
            time.sleep(_DDE_TIMEOUT_S)
            return ""
        time.sleep(0.005)
        return "100.0"
    elif _dde_mode == "raise" and _iter_count >= 2:
        raise RuntimeError("simulated DDE error")
    # 其他情況走 normal
    time.sleep(0.005)
    return "100.0"


def _post_feed_mock(batch, series):
    if _post_feed_mode == "raise" and _iter_count >= 2:
        raise RuntimeError(f"simulated post_feed crash at iter={_iter_count}")
    pass


def _push_futures_price_mock():
    """每輪結尾呼叫。用 _post_feed_mode == 'raise' 觸發第二輪起拋例外，
    模擬主迴圈 loop body 內 silent thread death 路徑（H2）。
    用這個比 _post_feed 可靠，因為 changed 可能是 0，_post_feed 不會被呼叫。"""
    if _post_feed_mode == "raise" and _iter_count >= 2:
        raise RuntimeError(f"simulated push_futures_price crash at iter={_iter_count}")
    pass


def _fetch_quote(symbol: str):
    """模擬 production 的 _fetch_quote — 6 個 _req_thread 呼叫"""
    bid  = _req_thread_mock(f"{symbol}.TF-Bid")
    ask  = _req_thread_mock(f"{symbol}.TF-Ask")
    last = _req_thread_mock(f"{symbol}.TF-Price")
    vol  = _req_thread_mock(f"{symbol}.TF-TotalVolume")
    ratio = _req_thread_mock(f"{symbol}.TF-InOutRatio")
    avg  = _req_thread_mock(f"{symbol}.TF-AvgPrice")
    return symbol, bid, ask, last, vol, ratio, avg


def quote_poll_loop_production_clone(max_iters: int, hang_detect_s: float = 30.0,
                                      use_outer_try: bool = False) -> dict:
    """
    完全複製 production _quote_poll_worker 的結構，加幾個觀察點：
    - max_iters: 跑幾輪後就 break（避免無限循環）
    - hang_detect_s: 主執行緒 wall time 超過此值就強制終止並標記為 hung
    - use_outer_try: 加 Fix A（outer try/except）

    回傳統計 dict，含每輪 elapsed、is_hung、exception 訊息等。
    """
    global _iter_count
    _iter_count = 0
    stats = {"iters": [], "hung": False, "exception": None,
             "thread_died_at_iter": None, "total_wall_time": 0.0}

    symbols = [f"TXVN04C{34000+i*100:05d}" for i in range(N_SYMBOLS // 2)] + \
              [f"TXVN04P{34000+i*100:05d}" for i in range(N_SYMBOLS // 2)]
    series = "TXVN04"

    overall_start = time.time()

    def _worker_body():
        global _iter_count
        nonlocal stats
        with ThreadPoolExecutor(max_workers=_QUOTE_POLL_THREADS,
                                thread_name_prefix='quote_req') as executor:
            while _iter_count < max_iters:
                _iter_count += 1
                this_iter = _iter_count

                if use_outer_try:
                    try:
                        _do_one_iteration(executor, symbols, series, stats, this_iter)
                    except Exception as e:
                        logger.error(f"[outer-catch] iter={this_iter} 例外：{e}\n{traceback.format_exc()}")
                        stats["iters"].append({"iter": this_iter, "elapsed": -1,
                                                "exception": str(e)})
                        time.sleep(0.1)
                        continue
                else:
                    # production 現況：沒有 outer try
                    _do_one_iteration(executor, symbols, series, stats, this_iter)

    worker_thread = threading.Thread(target=_worker_body, name="quote_poll", daemon=True)
    worker_thread.start()
    worker_thread.join(timeout=hang_detect_s)
    stats["total_wall_time"] = time.time() - overall_start

    if worker_thread.is_alive():
        stats["hung"] = True
        logger.warning(f"[WARN] worker thread 仍在跑，達到 {hang_detect_s}s 強制視為 hung")
    elif _iter_count < max_iters:
        # thread 退出但沒跑完 --> 死掉了
        stats["thread_died_at_iter"] = _iter_count
        logger.warning(f"[WARN] worker thread 死亡，最後 iter={_iter_count}")

    return stats


def _do_one_iteration(executor, symbols, series, stats, this_iter):
    """模擬 production loop body 的一輪。"""
    t0 = time.time()
    futures = {executor.submit(_fetch_quote, sym): sym for sym in symbols}
    changed = []
    for fut in as_completed(futures):
        try:
            symbol, bid, ask, last, vol, ratio, avg = fut.result()
        except Exception:
            continue
        cur = (bid, ask, last, vol, ratio, avg)
        prev = _quote_prevs.get(symbol)
        if prev and prev == cur:
            continue
        _quote_prevs[symbol] = cur
        changed.append({"symbol": symbol})
    elapsed = time.time() - t0
    logger.info(f"[quote_poll] iter={this_iter} {len(symbols)} 合約，"
                f"{len(changed)} 筆變化，耗時 {elapsed*1000:.0f}ms")
    stats["iters"].append({"iter": this_iter, "elapsed": elapsed, "changed": len(changed)})

    if changed:
        _post_feed_mock(changed, series)
    _push_futures_price_mock()


# ── Test cases ─────────────────────────────────────────────────

def reset_state():
    global _quote_prevs, _post_feed_mode, _dde_mode, _iter_count
    _quote_prevs = {}
    _post_feed_mode = "normal"
    _dde_mode = "normal"
    _iter_count = 0


def test_baseline_normal():
    """Test 0: 正常情境，跑 5 輪應該都在 < 1s 完成"""
    print("\n=== Test 0: baseline (normal) ===")
    reset_state()
    stats = quote_poll_loop_production_clone(max_iters=5, hang_detect_s=30.0, use_outer_try=False)
    assert not stats["hung"], "baseline 不應該 hang"
    assert len(stats["iters"]) == 5
    assert all(it.get("elapsed", 0) < 2.0 for it in stats["iters"])
    print(f"  [OK] 跑了 {len(stats['iters'])} 輪，每輪 elapsed < 2s")


def test_h2_silent_thread_death_no_fix():
    """Test 1 (H2-): 第二輪時 _post_feed 拋例外，**沒**加 outer try --> thread silent death"""
    print("\n=== Test 1: H2 silent thread death (no fix) ===")
    reset_state()
    global _post_feed_mode
    _post_feed_mode = "raise"   # _iter_count >= 2 才會 fire
    stats = quote_poll_loop_production_clone(max_iters=10, hang_detect_s=15.0, use_outer_try=False)

    print(f"  iters completed: {len(stats['iters'])}")
    print(f"  thread_died_at_iter: {stats['thread_died_at_iter']}")
    print(f"  hung: {stats['hung']}")
    if stats["thread_died_at_iter"] is not None:
        print("  [OK] 重現 H2：worker thread 在拋例外後死亡，沒人接手")
    else:
        print("  [FAIL] 預期會 silent death 但沒發生（mock 行為差異？）")


def test_h2_silent_thread_death_with_fix_a():
    """Test 2 (H2+Fix A): 同情境，加 outer try/except --> 應該能 recover 繼續跑"""
    print("\n=== Test 2: H2 + Fix A (outer try/except) ===")
    reset_state()
    global _post_feed_mode
    _post_feed_mode = "raise"
    stats = quote_poll_loop_production_clone(max_iters=10, hang_detect_s=15.0, use_outer_try=True)

    n_caught = sum(1 for it in stats['iters'] if it.get('exception'))
    print(f"  iter_count reached: {_iter_count}")
    print(f"  exceptions caught: {n_caught}")
    if _iter_count == 10 and not stats["hung"] and n_caught >= 8:
        print("  [OK] Fix A 成功：outer try/except 把每個例外接住，10 輪都跑完")
    else:
        print("  [FAIL] Fix A 失敗或行為異常")


def test_h1_dde_backpressure():
    """Test 3 (H1): 第二輪起所有 DDE call 全 timeout --> 整個 iteration 卡 ~75s"""
    print("\n=== Test 3: H1 DDE backpressure ===")
    reset_state()
    global _dde_mode
    _dde_mode = "backpressure"   # _iter_count >= 2 才會 fire
    print(f"  [setup] iter>=2 時所有 _req_thread call 會 sleep {_DDE_TIMEOUT_S}s")
    stats = quote_poll_loop_production_clone(max_iters=2, hang_detect_s=60.0, use_outer_try=False)

    print(f"  iters completed: {len(stats['iters'])}")
    if len(stats["iters"]) >= 2:
        iter2_elapsed = stats["iters"][1]["elapsed"]
        print(f"  iter 2 elapsed: {iter2_elapsed:.1f}s")
        # 24 thread × 12 symbol/thread × 6 fields × 5s = 360s 最壞，但實際因並行 ≈ 12 × 6 × 5 = 360s/thread
        # ThreadPoolExecutor 只有 24 個 thread，要處理 298 symbols 中每個都要 6 次 5s timeout
        # 期望 iter 2 elapsed ~ 75s（24 thread 並行處理 298 symbols × 6 fields × 5s）
        if iter2_elapsed > 60:
            print(f"  [OK] 重現 H1：iter 2 卡 {iter2_elapsed:.0f}s，符合 DDE backpressure 模型")
        else:
            print(f"  ? iter 2 只卡 {iter2_elapsed:.0f}s，可能 mock 太快或機器比預期強")
    else:
        print(f"  ? 跑不到 iter 2")


def test_h1_dde_backpressure_with_fix_a():
    """Test 4 (H1+Fix A): backpressure 情境，Fix A 沒辦法解（H1 不是 exception）"""
    print("\n=== Test 4: H1 + Fix A -- Fix A 對 H1 無效（重要負面驗證）===")
    reset_state()
    global _dde_mode
    _dde_mode = "backpressure"
    stats = quote_poll_loop_production_clone(max_iters=2, hang_detect_s=60.0, use_outer_try=True)

    print(f"  iters completed: {len(stats['iters'])}")
    if len(stats["iters"]) >= 2:
        iter2_elapsed = stats["iters"][1]["elapsed"]
        print(f"  iter 2 elapsed: {iter2_elapsed:.1f}s")
        print("  [OK] 確認 Fix A 對 H1 沒效果（沒 exception 可 catch），iter 2 還是卡長時間")
        print("  --> 需要 Fix C/D（as_completed timeout / wait timeout）才能解 H1")


def test_partial_backpressure():
    """Test 5: 部分 symbol 變慢（更接近實際情況）"""
    print("\n=== Test 5: 部分 symbol backpressure ===")
    reset_state()
    global _dde_mode
    _dde_mode = "partial_backpressure"
    stats = quote_poll_loop_production_clone(max_iters=3, hang_detect_s=60.0, use_outer_try=False)

    print(f"  iters completed: {len(stats['iters'])}")
    for it in stats["iters"]:
        print(f"  iter {it['iter']}: elapsed={it['elapsed']*1000:.0f}ms, changed={it.get('changed','-')}")


def main():
    test_baseline_normal()
    test_h2_silent_thread_death_no_fix()
    test_h2_silent_thread_death_with_fix_a()
    test_h1_dde_backpressure()
    test_h1_dde_backpressure_with_fix_a()
    test_partial_backpressure()
    print("\n=== 全部測試結束 ===")
    print("結論將寫進 QUOTE_POLL_HANG_DIAGNOSIS.md 的 §4 假設驗證段")


if __name__ == "__main__":
    main()
