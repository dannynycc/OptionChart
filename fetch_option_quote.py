"""
富邦期貨 API - 選擇權報價抓取
使用 fubon_neo SDK
"""

from fubon_neo.sdk import FubonSDK

# ── 登入設定 ──────────────────────────────────────────────
ID        = "YOUR_ID"
PASSWORD  = "YOUR_PASSWORD"
CERT_PATH = "YOUR_CERT_PATH"   # 憑證路徑，例如 "/path/to/cert.pfx"
CERT_PASS = "YOUR_CERT_PASS"   # 憑證密碼
# ─────────────────────────────────────────────────────────

def login():
    sdk = FubonSDK()
    accounts = sdk.login(ID, PASSWORD, CERT_PATH, CERT_PASS)
    sdk.init_realtime()
    return sdk

def get_option_tickers(sdk, contract_type="I", session="REGULAR"):
    """
    取得所有選擇權商品列表
    contract_type: I=指數, S=股票, R=利率, B=債券, C=商品, E=匯率
    """
    restfut = sdk.marketdata.rest_client.futopt
    result = restfut.intraday.tickers(
        type="OPTION",
        exchange="TAIFEX",
        session=session,
        contract_type=contract_type,
    )
    return result

def get_option_quote(sdk, symbol, session="REGULAR"):
    """
    取得單一選擇權即時報價
    symbol: 選擇權代碼，例如 'TXOA4C17000' (台指選擇權)
    """
    restfut = sdk.marketdata.rest_client.futopt
    result = restfut.intraday.quote(symbol=symbol, session=session)
    return result

def print_quote(quote):
    print(f"商品代碼  : {quote.get('symbol')}")
    print(f"商品名稱  : {quote.get('name')}")
    print(f"最新成交價: {quote.get('lastPrice')}")
    print(f"漲跌      : {quote.get('change')} ({quote.get('changePercent')}%)")
    print(f"開盤價    : {quote.get('openPrice')}")
    print(f"最高價    : {quote.get('highPrice')}")
    print(f"最低價    : {quote.get('lowPrice')}")
    print(f"昨收價    : {quote.get('previousClose')}")
    print(f"成交量    : {quote.get('total', {}).get('tradeVolume')}")
    last = quote.get('lastTrade', {})
    print(f"最佳買價  : {last.get('bid')}")
    print(f"最佳賣價  : {last.get('ask')}")

if __name__ == "__main__":
    sdk = login()

    # 1. 列出所有台指選擇權商品代碼
    print("=== 台指選擇權商品列表 ===")
    tickers = get_option_tickers(sdk, contract_type="I")
    if tickers and "data" in tickers:
        for item in tickers["data"]:
            print(f"  {item.get('symbol'):20s} {item.get('name')}")

    # 2. 抓特定選擇權報價（請修改為實際代碼）
    symbol = "TXOA4C17000"  # 範例：台指選擇權 Call
    print(f"\n=== {symbol} 即時報價 ===")
    quote = get_option_quote(sdk, symbol)
    if quote:
        print_quote(quote)
