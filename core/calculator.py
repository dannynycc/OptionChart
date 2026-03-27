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
    trade_volume: int = 0     # 日+夜 合計（TotalVolume，含開盤競價）
    inout_ratio: float = 50.0 # 外盤比 0~100（直接來自 XQFAP InOutRatio = OutSize/TotalVolume×100）
    bid_match: int = 0        # 外盤口數（Buy）= round(inout_ratio/100 × trade_volume），供顯示用
    ask_match: int = 0        # 內盤口數（Sell）= trade_volume - bid_match，供顯示用
    trade_volume_day: int = -1  # 純日盤；-1 = 未提供（群益橋接），回退用合計值
    bid_match_day: int = -1
    ask_match_day: int = -1
    avg_price: float = 0.0
    prev_close: float = 0.0  # 備用：盤後 avgPrice 為空時用前日收盤價

    @property
    def avg_premium(self) -> float:
        """權利金：優先用 avgPrice，無成交時用前日收盤價"""
        return self.avg_price if self.avg_price > 0 else self.prev_close

    @property
    def net_position(self) -> float:
        """
        淨口數 = ROUND((InOutRatio-50)/50 × TotalVolume, 0)
        與 Excel Golden 公式一致。
        InOutRatio = OutSize/TotalVolume×100，分母含開盤競價，
        故不能用 OutSize-InSize（分母不同）。
        """
        return round((self.inout_ratio - 50) / 50 * self.trade_volume)


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
    計算所有履約價作為假設結算價時的全市場淨損益曲線。

    每個履約價的貢獻 = net_position × (內含價值 - 均價)
    net_position 已編碼淨方向（正→淨買方、負→淨賣方），
    因此自動涵蓋 Call買方/賣方、Put買方/賣方 四方。

    回傳：
    {
        "strikes": [履約價列表，升序],
        "pnl":     [對應的合併損益（億元）],
    }
    """
    all_strikes = sorted(set(
        [c.strike for c in calls] + [p.strike for p in puts]
    ))

    if not all_strikes:
        return {"strikes": [], "pnl": []}

    strikes_out = []
    pnl_out = []

    for settlement in all_strikes:
        call_pnl = _calc_call_pnl(settlement, calls)
        put_pnl  = _calc_put_pnl(settlement, puts)
        strikes_out.append(settlement)
        pnl_out.append(round(call_pnl + put_pnl, 4))

    return {
        "strikes": strikes_out,
        "pnl":     pnl_out,
    }


def _calc_call_pnl(settlement: int, calls: list[OptionData]) -> float:
    """
    CALL 全市場淨損益（億元）
    Σ[ max(settlement - strike, 0) - avgPremium ] × netPosition
    × 50 / 1億
    netPosition = bid_match - ask_match（今日淨方向性流量）
    """
    total = 0.0
    for c in calls:
        intrinsic  = max(settlement - c.strike, 0)
        pnl_points = intrinsic - c.avg_premium
        total += c.net_position * pnl_points
    return (total * 50) / 100_000_000


def _calc_put_pnl(settlement: int, puts: list[OptionData]) -> float:
    """
    PUT 全市場淨損益（億元）
    Σ[ max(strike - settlement, 0) - avgPremium ] × netPosition
    × 50 / 1億
    netPosition = bid_match - ask_match（今日淨方向性流量）
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

        # 日盤欄位（-1 代表未提供，回退用日+夜合計）
        c_bid_day  = c.bid_match_day   if c and c.bid_match_day   >= 0 else (c.bid_match   if c else 0)
        c_ask_day  = c.ask_match_day   if c and c.ask_match_day   >= 0 else (c.ask_match   if c else 0)
        c_vol_day  = c.trade_volume_day if c and c.trade_volume_day >= 0 else (c.trade_volume if c else 0)
        p_bid_day  = p.bid_match_day   if p and p.bid_match_day   >= 0 else (p.bid_match   if p else 0)
        p_ask_day  = p.ask_match_day   if p and p.ask_match_day   >= 0 else (p.ask_match   if p else 0)
        p_vol_day  = p.trade_volume_day if p and p.trade_volume_day >= 0 else (p.trade_volume if p else 0)

        c_net_day   = float(c_bid_day - c_ask_day)
        p_net_day   = float(p_bid_day - p_ask_day)
        c_ratio_day = round(c_ask_day / (c_bid_day + c_ask_day) * 100, 1) if (c_bid_day + c_ask_day) > 0 else 50.0
        p_ratio_day = round(p_ask_day / (p_bid_day + p_ask_day) * 100, 1) if (p_bid_day + p_ask_day) > 0 else 50.0

        call_pnl     = _calc_call_pnl(strike, calls)
        put_pnl      = _calc_put_pnl(strike, puts)
        combined_pnl = call_pnl + put_pnl

        rows.append({
            "strike":    strike,
            # 日+夜 合計
            "net_call":   c.net_position if c else 0,
            "vol_call":   c.trade_volume if c else 0,
            "ratio_call": round(c.inout_ratio, 2) if c else 50.0,
            "net_put":    p.net_position if p else 0,
            "vol_put":    p.trade_volume if p else 0,
            "ratio_put":  round(p.inout_ratio, 2) if p else 50.0,
            "avg_price_call": round(c.avg_premium, 2) if c else 0.0,
            "ask_match_call": c.ask_match if c else 0,
            "bid_match_call": c.bid_match if c else 0,
            "avg_price_put":  round(p.avg_premium, 2) if p else 0.0,
            "ask_match_put":  p.ask_match if p else 0,
            "bid_match_put":  p.bid_match if p else 0,
            # 純日盤
            "net_call_day":      c_net_day,
            "vol_call_day":      c_vol_day,
            "ratio_call_day":    c_ratio_day,
            "ask_match_call_day": c_ask_day,
            "bid_match_call_day": c_bid_day,
            "net_put_day":       p_net_day,
            "vol_put_day":       p_vol_day,
            "ratio_put_day":     p_ratio_day,
            "ask_match_put_day": p_ask_day,
            "bid_match_put_day": p_bid_day,
            "highlight": strike == highlight_strike,
            # 損益驗證欄（假設結算於此履約價時的全市場淨損益）
            "pnl_call":     round(call_pnl,     4),
            "pnl_put":      round(put_pnl,      4),
            "pnl_combined": round(combined_pnl, 4),
        })
    return rows
