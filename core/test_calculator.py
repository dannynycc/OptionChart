"""
calculator.py 單元測試
用手算數值驗證邏輯是否與 Excel VBA 一致
"""

from core.calculator import OptionData, calc_combined_pnl, build_strike_table, parse_strike, parse_side


def test_parse_strike():
    assert parse_strike("TX429400C6", "臺指選擇權W4036;29400買權") == 29400
    assert parse_strike("TX429500O6", "臺指選擇權W4036;29500賣權") == 29500
    assert parse_strike("TX430000C6", "臺指選擇權W4036;30000買權") == 30000
    print("✅ parse_strike OK")


def test_parse_side():
    assert parse_side("臺指選擇權W4036;29400買權") == 'C'
    assert parse_side("臺指選擇權W4036;29400賣權") == 'P'
    print("✅ parse_side OK")


def test_net_position():
    # InOutRatio=60, volume=1000 → (60-50)/50*1000 = 200
    o = OptionData(symbol="X", strike=29400, side='C',
                   trade_volume=1000, bid_match=600, ask_match=400)
    assert o.inout_ratio == 60.0
    assert o.net_position == 200.0

    # InOutRatio=40, volume=500 → (40-50)/50*500 = -100
    o2 = OptionData(symbol="X", strike=29400, side='C',
                    trade_volume=500, bid_match=200, ask_match=300)
    assert o2.inout_ratio == 40.0
    assert o2.net_position == -100.0

    # 無成交 → InOutRatio=50 → net=0
    o3 = OptionData(symbol="X", strike=29400, side='C',
                    trade_volume=0, bid_match=0, ask_match=0)
    assert o3.inout_ratio == 50.0
    assert o3.net_position == 0.0
    print("✅ net_position OK")


def test_pnl_simple():
    """
    手算驗證（仿 Excel VBA 邏輯）：

    設定：
      1 個 Call，履約價=100，淨部位=10，均價=5
      1 個 Put，  履約價=100，淨部位=8， 均價=3

    假設結算價=110：
      Call intrinsic = max(110-100, 0) = 10
      Call pnl_pts   = 10 - 5 = 5
      Call total_pts = 10 * 5 = 50
      Call 億元      = 50 * 50 / 1e8 = 0.000025

      Put  intrinsic = max(100-110, 0) = 0
      Put  pnl_pts   = 0 - 3 = -3
      Put  total_pts = 8 * (-3) = -24
      Put  億元      = -24 * 50 / 1e8 = -0.000012

      combined = 0.000025 + (-0.000012) = 0.000013
    """
    calls = [OptionData("C", 100, 'C', avg_price=5.0,
                        bid_match=60, ask_match=40, trade_volume=100)]
    puts  = [OptionData("P", 100, 'P', avg_price=3.0,
                        bid_match=60, ask_match=40, trade_volume=80)]
    # net_call = (60/100*100-50)/50*100 = (60-50)/50*100 = 20
    # net_put  = (60/100*100-50)/50*80  = 16

    result = calc_combined_pnl(calls, puts)

    # 手算：settlement=100
    # Call intrinsic=0, pnl=-5, total=-5*20=-100 → -100*50/1e8 = -0.000005
    # Put  intrinsic=0, pnl=-3, total=-3*16=-48  → -48*50/1e8  = -0.000024
    # combined = -0.000029

    assert result["strikes"] == [100]
    assert result["max_pain"] == 100
    print(f"  combined pnl at strike=100: {result['pnl'][0]:.8f}")
    print("✅ pnl_simple OK")


def test_pnl_max_pain():
    """
    3 個履約價，驗證 Max Pain 在正確位置
    """
    calls = [
        OptionData("C90", 90, 'C', avg_price=15.0, bid_match=6, ask_match=4, trade_volume=100),
        OptionData("C100",100,'C', avg_price=8.0,  bid_match=6, ask_match=4, trade_volume=200),
        OptionData("C110",110,'C', avg_price=3.0,  bid_match=6, ask_match=4, trade_volume=50),
    ]
    puts = [
        OptionData("P90", 90, 'P', avg_price=3.0,  bid_match=6, ask_match=4, trade_volume=50),
        OptionData("P100",100,'P', avg_price=8.0,  bid_match=6, ask_match=4, trade_volume=200),
        OptionData("P110",110,'P', avg_price=15.0, bid_match=6, ask_match=4, trade_volume=100),
    ]
    result = calc_combined_pnl(calls, puts)
    print(f"  strikes:  {result['strikes']}")
    print(f"  pnl:      {[round(v,6) for v in result['pnl']]}")
    print(f"  max_pain: {result['max_pain']} ({result['max_pain_value']:.6f} 億元)")
    assert result["max_pain"] in [90, 100, 110]
    print("✅ pnl_max_pain OK")


def test_build_strike_table():
    calls = [
        OptionData("C100", 100, 'C', bid_match=6, ask_match=4, trade_volume=100),
        OptionData("C200", 200, 'C', bid_match=3, ask_match=7, trade_volume=50),
    ]
    puts = [
        OptionData("P100", 100, 'P', bid_match=5, ask_match=5, trade_volume=80),
        OptionData("P200", 200, 'P', bid_match=8, ask_match=2, trade_volume=60),
    ]
    table = build_strike_table(calls, puts, current_index=105)

    # 高履約價在上
    assert table[0]['strike'] == 200
    assert table[1]['strike'] == 100

    # 高亮最接近 current_index=105 的履約價 → 100
    assert table[1]['highlight'] == True
    assert table[0]['highlight'] == False

    print(f"  table[0]: {table[0]}")
    print(f"  table[1]: {table[1]}")
    print("✅ build_strike_table OK")


if __name__ == "__main__":
    test_parse_strike()
    test_parse_side()
    test_net_position()
    test_pnl_simple()
    test_pnl_max_pain()
    test_build_strike_table()
    print("\n全部測試通過 ✅")
