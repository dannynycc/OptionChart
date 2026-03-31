"""
taifex_calendar.py — TAIFEX 臺指選擇權合約命名規則、結算日推導、有效系列掃描

【命名規則】
  前綴代碼（TAIFEX）：
    週三到期：TX1=W1, TX2=W2, TXO=W3(月選), TX4=W4, TX5=W5(罕見)
    週五到期：TXU=F1, TXV=F2, TXX=F3, TXY=F4, TXZ=F5(罕見)
    ※ TX3 不存在（第3個週三 = 月選 TXO）
    ※ TX5/TXZ 只在該月剛好有第5個週三/週五時才存在

  XQFAP DDE 命名格式：
    全日盤（夜+日）：{前綴}N{2位月份}  e.g. TX4N03, TXYN03, TXON04
    日盤（一般）   ：{前綴}{2位月份}   e.g. TX403,  TXY03,  TXO04
    ※ N 固定為字母 N，代表 Night+Day

  XQFAP TF-Name 欄位標籤：
    週三到期：台指選{月}W{週次}  e.g. 03W4
    週五到期：台指選{月}F{週次}  e.g. 03F4
    月選    ：台指選{月}         e.g. 04

【結算日計算邏輯】
  1. 找當月第 N 個指定週幾（週三或週五）
  2. 若該日為休市日 → 往後順延至最近交易日
  休市日來源：https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=html&year={YYYY}

【有效合約存在規律】
  - 當月 + 下個月：10 個前綴都可能存在（視 TAIFEX 是否掛牌）
  - 第3個月起：只有月選 TXO 存在（週選尚未掛牌）
  - 不死背清單，用 scan_valid_series() 動態查詢 XQFAP
"""

import datetime
import re
from functools import lru_cache

# ── 前綴定義 ──────────────────────────────────────────────────

# (前綴, 週次, 到期星期)  weekday: 2=週三, 4=週五
PREFIX_RULES: list[tuple[str, int, int]] = [
    ("TX1",  1, 2),   # 第1個週三
    ("TX2",  2, 2),   # 第2個週三
    ("TXO",  3, 2),   # 第3個週三（月選）
    ("TX4",  4, 2),   # 第4個週三
    ("TX5",  5, 2),   # 第5個週三（罕見）
    ("TXU",  1, 4),   # 第1個週五
    ("TXV",  2, 4),   # 第2個週五
    ("TXX",  3, 4),   # 第3個週五
    ("TXY",  4, 4),   # 第4個週五
    ("TXZ",  5, 4),   # 第5個週五（罕見）
]

ALL_PREFIXES     = [p for p, _, _ in PREFIX_RULES]
WEEKLY_PREFIXES  = [p for p, _, _ in PREFIX_RULES if p != "TXO"]


# ── 結算日計算 ────────────────────────────────────────────────

def nth_weekday(year: int, month: int, n: int, weekday: int) -> datetime.date | None:
    """當月第 n 個 weekday（0=週一…6=週日）。不存在回傳 None（如第5個週三）。"""
    count = 0
    day = datetime.date(year, month, 1)
    while day.month == month:
        if day.weekday() == weekday:
            count += 1
            if count == n:
                return day
        day += datetime.timedelta(days=1)
    return None


@lru_cache(maxsize=4)
def fetch_holidays(year: int) -> set[datetime.date]:
    """從 TWSE 取得指定年度所有休市日。結果 LRU 快取，不重複 fetch。"""
    try:
        import urllib.request, ssl
        url = (f"https://www.twse.com.tw/rwd/zh/holidaySchedule/"
               f"holidaySchedule?response=html&year={year}")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
            html = r.read().decode("utf-8", errors="replace")
        holidays = set()
        for m in re.finditer(r'(\d{4})[/-](\d{2})[/-](\d{2})', html):
            try:
                holidays.add(datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
        return holidays
    except Exception:
        return set()


def next_trading_day(date: datetime.date, holidays: set[datetime.date]) -> datetime.date:
    """若 date 為休市日（週六/日或假日），順延至最近交易日。"""
    d = date
    while d.weekday() >= 5 or d in holidays:
        d += datetime.timedelta(days=1)
    return d


def settlement_date(prefix: str, year: int, month: int) -> datetime.date | None:
    """
    計算指定前綴 + 年月的實際結算日（含休市順延）。
    找不到名義到期日（如當月無第5個週三）回傳 None。
    """
    rule = next(((n, wd) for p, n, wd in PREFIX_RULES if p == prefix), None)
    if rule is None:
        return None
    n, wd = rule
    nominal = nth_weekday(year, month, n, wd)
    if nominal is None:
        return None
    holidays = fetch_holidays(year)
    return next_trading_day(nominal, holidays)


# ── 系列名稱工具 ──────────────────────────────────────────────

def series_full(prefix: str, month: int) -> str:
    """全日盤系列碼，e.g. series_full('TX4', 3) → 'TX4N03'"""
    return f"{prefix}N{month:02d}"


def series_day(prefix: str, month: int) -> str:
    """日盤系列碼，e.g. series_day('TX4', 3) → 'TX403'"""
    return f"{prefix}{month:02d}"


def tf_name_label(prefix: str, month: int) -> str:
    """
    回傳 XQFAP TF-Name 欄位的系列標籤。
      週三（非月選）→ '{MM}W{n}'  e.g. '03W4'
      週五          → '{MM}F{n}'  e.g. '03F4'
      月選 TXO      → '{MM}'      e.g. '04'
    """
    rule = next(((n, wd) for p, n, wd in PREFIX_RULES if p == prefix), None)
    if rule is None:
        return f"{month:02d}"
    n, wd = rule
    if prefix == "TXO":
        return f"{month:02d}"
    elif wd == 2:
        return f"{month:02d}W{n}"
    else:
        return f"{month:02d}F{n}"


def day_from_full(full_series: str) -> str:
    """全日盤 → 日盤，e.g. 'TX4N03' → 'TX403'（去掉 N）"""
    # 找 N 的位置（固定在前綴後面）
    idx = full_series.index('N')
    return full_series[:idx] + full_series[idx + 1:]


# ── 有效合約掃描（需搭配 XQFAP DDE）────────────────────────────

def build_scan_plan(center: int) -> list[tuple[str, list[str]]]:
    """
    建立掃描計畫：回傳 [(series_full, [test_symbols]), ...] 共 120 組。
    - 當月 + 下個月：10 個前綴
    - 第3～第12個月：只掃 TXO
    - 測試點：center, +50, +100, +150（其中一個必中）
    """
    plan = []
    now = datetime.datetime.now()
    test_offsets = [0, 50, 100, 150]

    for month_offset in range(12):
        m = now.month + month_offset
        month = ((m - 1) % 12) + 1
        prefixes = ALL_PREFIXES if month_offset < 2 else ["TXO"]

        for prefix in prefixes:
            sf   = series_full(prefix, month)
            syms = [f"{sf}C{center + o}" for o in test_offsets]
            plan.append((sf, syms))

    return plan  # 120 筆
