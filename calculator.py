"""
calculator.py
純計算邏輯，不依賴任何富邦 SDK。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OptionData:
    symbol: str
    strike: int
    side: str          # 'C' = Call, 'P' = Put
    trade_volume: int = 0
    bid_match: int = 0
    ask_match: int = 0
    avg_price: float = 0.0
    prev_close: float = 0.0  # 備用：盤後 avgPrice 為空時用前日收盤價

    @property
    def avg_premium(self) -> float:
        """權利金：優先用 avgPrice，無成交時用前日收盤價"""
        return self.avg_price if self.avg_price > 0 else self.prev_close

    @property
    def inout_ratio(self) -> float:
        """外盤比 (0~100)：外盤(nTBc=ask_match) 占總量比例，與 XQFAP 一致"""
        total = self.bid_match + self.ask_match
        if total == 0:
            return 50.0
        return self.ask_match / total * 100

    @property
    def net_position(self) -> float:
        """
        淨CALL 或 淨PUT = nTAc - nTBc（內盤 - 外盤）
        不依賴 trade_volume，因 trade_volume=nTQty 含開盤競價，
        而 nTAc/nTBc 只累計方向性成交，兩者範圍不同。
        """
        return float(self.bid_match - self.ask_match)


def parse_strike(symbol: str, name: str) -> int:
    """
    從 symbol 或 name 取出履約價
    symbol 格式：TX429400C6 → 履約價 29400
    name 格式：臺指選擇權W4036;29400買權 → 履約價 29400
    """
    # 優先從 name 解析（分號後、買權/賣權前）
    if ';' in name:
        try:
            part = name.split(';')[1]          # "29400買權"
            strike_str = part.replace('買權', '').replace('賣權', '')
            return int(strike_str)
        except (ValueError, IndexError):
            pass
    # fallback：從 symbol 取中間數字
    # TX4 + 29400 + C6 → 去掉前3碼和後2碼
    try:
        mid = symbol[3:-2]    # "29400"
        return int(mid)
    except ValueError:
        return 0


def parse_side(name: str) -> str:
    """從 name 判斷 Call/Put"""
    if '買權' in name:
        return 'C'
    if '賣權' in name:
        return 'P'
    return '?'


def calc_combined_pnl(
    calls: list[OptionData],
    puts: list[OptionData],
) -> dict:
    """
    計算所有履約價作為假設結算價時的買方合併損益曲線。

    回傳：
    {
        "strikes": [履約價列表，升序],
        "pnl":     [對應的合併損益（億元）],
        "max_pain": 最小損益點的履約價（Max Pain）,
        "max_pain_value": 最小損益值（億元）,
    }
    """
    # 取所有履約價的聯集，排序
    all_strikes = sorted(set(
        [c.strike for c in calls] + [p.strike for p in puts]
    ))

    if not all_strikes:
        return {"strikes": [], "pnl": [], "max_pain": None, "max_pain_value": None}

    strikes_out = []
    pnl_out = []

    for settlement in all_strikes:
        call_pnl = _calc_call_pnl(settlement, calls)
        put_pnl  = _calc_put_pnl(settlement, puts)
        combined = call_pnl + put_pnl
        strikes_out.append(settlement)
        pnl_out.append(round(combined, 4))

    # Max Pain = 合併損益最小值對應的履約價
    min_idx   = pnl_out.index(min(pnl_out))
    max_pain  = strikes_out[min_idx]
    max_pain_value = pnl_out[min_idx]

    return {
        "strikes":        strikes_out,
        "pnl":            pnl_out,
        "max_pain":       max_pain,
        "max_pain_value": max_pain_value,
    }


def _calc_call_pnl(settlement: int, calls: list[OptionData]) -> float:
    """
    CALL 買方總損益（億元）
    Σ[ max(settlement - strike, 0) - avgPremium ] × netPosition
    × 50 / 1億
    """
    total = 0.0
    for c in calls:
        intrinsic  = max(settlement - c.strike, 0)
        pnl_points = intrinsic - c.avg_premium
        total += c.net_position * pnl_points
    return (total * 50) / 100_000_000


def _calc_put_pnl(settlement: int, puts: list[OptionData]) -> float:
    """
    PUT 買方總損益（億元）
    Σ[ max(strike - settlement, 0) - avgPremium ] × netPosition
    × 50 / 1億
    """
    total = 0.0
    for p in puts:
        intrinsic  = max(p.strike - settlement, 0)
        pnl_points = intrinsic - p.avg_premium
        total += p.net_position * pnl_points
    return (total * 50) / 100_000_000


def build_strike_table(
    calls: list[OptionData],
    puts: list[OptionData],
    current_index: Optional[int] = None,
) -> list[dict]:
    """
    組合左側 T 字報價表資料，供前端渲染。
    回傳 list，每個 dict 代表一列（一個履約價）：
    {
        "strike":       履約價,
        "net_call":     淨CALL 值,
        "net_put":      淨PUT 值,
        "highlight":    bool（是否為目前指數最近的履約價）,
    }
    依履約價降序排列（高在上）。
    """
    call_map = {c.strike: c for c in calls}
    put_map  = {p.strike: p for p in puts}
    all_strikes = sorted(
        set(list(call_map.keys()) + list(put_map.keys())),
        reverse=False  # 低履約價在上
    )

    # 找最接近 current_index 的履約價
    highlight_strike = None
    if current_index is not None and all_strikes:
        highlight_strike = min(all_strikes, key=lambda s: abs(s - current_index))

    rows = []
    for strike in all_strikes:
        c = call_map.get(strike)
        p = put_map.get(strike)
        rows.append({
            "strike":    strike,
            "net_call":  c.net_position if c else 0,
            "vol_call":  c.trade_volume if c else 0,
            "ratio_call": round(c.inout_ratio, 1) if c else 50.0,
            "net_put":   p.net_position if p else 0,
            "vol_put":   p.trade_volume if p else 0,
            "ratio_put": round(p.inout_ratio, 1) if p else 50.0,
            "highlight": strike == highlight_strike,
        })
    return rows
