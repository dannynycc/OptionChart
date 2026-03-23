"""
fubon_client.py
富邦 WebSocket 連線管理。
負責：登入、取得近月合約列表、訂閱 trades channel、斷線重連。
"""

import json
import time
import threading
import logging
from typing import Callable, Optional
from fubon_neo.sdk import FubonSDK
from calculator import OptionData, parse_strike, parse_side

logger = logging.getLogger(__name__)


class FubonClient:
    def __init__(self, id: str, password: str, cert_path: str, cert_pass: str):
        self.id        = id
        self.password  = password
        self.cert_path = cert_path
        self.cert_pass = cert_pass

        self.sdk: Optional[FubonSDK] = None
        self.ws_list: list = []          # 多條 WebSocket（每條最多 200 個標的）

        # 資料儲存：key = symbol, value = OptionData
        self.store: dict[str, OptionData] = {}

        # 當資料有更新時呼叫此 callback（由 main.py 設定）
        self.on_update: Optional[Callable] = None

        # 近月結算日
        self.settlement_date: Optional[str] = None

        # 連線狀態
        self.connected = False
        self.subscribed_count = 0
        self.last_updated: Optional[float] = None
        self.error_message: Optional[str] = None

        self._lock = threading.Lock()

    # ── 對外介面 ───────────────────────────────────────────

    def start(self):
        """登入、取合約清單、訂閱 WebSocket"""
        self._login()
        symbols = self._fetch_near_month_symbols()
        self._subscribe_all(symbols)

    def get_store_snapshot(self) -> tuple[list[OptionData], list[OptionData]]:
        """回傳目前 store 的 call/put 快照（thread-safe）"""
        with self._lock:
            calls = [v for v in self.store.values() if v.side == 'C']
            puts  = [v for v in self.store.values() if v.side == 'P']
        return calls, puts

    def get_status(self) -> dict:
        return {
            "connected":       self.connected,
            "subscribed_count": self.subscribed_count,
            "settlement_date": self.settlement_date,
            "last_updated":    self.last_updated,
            "error":           self.error_message,
        }

    # ── 登入 ───────────────────────────────────────────────

    def _login(self):
        logger.info("登入富邦 API...")
        self.sdk = FubonSDK()
        accounts = self.sdk.login(
            self.id, self.password, self.cert_path, self.cert_pass
        )
        logger.info(f"登入成功：{accounts.data[0]}")
        self.sdk.init_realtime()

    # ── 取近月合約清單 ─────────────────────────────────────

    def _fetch_near_month_symbols(self) -> list[str]:
        """取出近月月選所有 Call + Put 的 symbol 列表，同時初始化 store"""
        logger.info("取得近月 TXO 合約列表...")
        restfut = self.sdk.marketdata.rest_client.futopt
        result  = restfut.intraday.tickers(
            type="OPTION",
            exchange="TAIFEX",
            session="REGULAR",
        )
        raw = result.data if hasattr(result, 'data') else result.get('data', [])

        def gf(obj, key):
            return obj.get(key, '') if isinstance(obj, dict) else getattr(obj, key, '')

        tickers = [
            {
                'symbol':         gf(t, 'symbol'),
                'name':           gf(t, 'name'),
                'settlementDate': gf(t, 'settlementDate'),
                'referencePrice': gf(t, 'referencePrice') or 0,
            }
            for t in raw
            if str(gf(t, 'symbol')).startswith('TX')
        ]

        # 找近月結算日（最近一個）
        dates = sorted(set(t['settlementDate'] for t in tickers if t['settlementDate']))
        if not dates:
            raise RuntimeError("找不到結算日，請確認 API 回傳資料")

        self.settlement_date = dates[0]
        logger.info(f"近月結算日：{self.settlement_date}")

        near = [t for t in tickers if t['settlementDate'] == self.settlement_date]
        logger.info(f"近月合約數：{len(near)}")

        symbols = []
        with self._lock:
            for t in near:
                sym  = t['symbol']
                name = t['name']
                side = parse_side(name)
                if side == '?':
                    continue
                strike = parse_strike(sym, name)
                self.store[sym] = OptionData(
                    symbol    = sym,
                    strike    = strike,
                    side      = side,
                    prev_close = float(t['referencePrice']),
                )
                symbols.append(sym)

        self.subscribed_count = len(symbols)
        logger.info(f"初始化 store：{len(symbols)} 個合約")
        return symbols

    # ── WebSocket 訂閱 ─────────────────────────────────────

    def _subscribe_all(self, symbols: list[str]):
        """將 symbols 切成每批最多 200 個，各開一條 WebSocket"""
        batch_size = 200
        batches = [symbols[i:i+batch_size] for i in range(0, len(symbols), batch_size)]
        logger.info(f"共 {len(symbols)} 個合約，分 {len(batches)} 條 WebSocket 訂閱")

        for idx, batch in enumerate(batches):
            self.sdk.init_realtime()
            ws = self.sdk.marketdata.websocket_client.futopt
            ws._batch = batch        # 暫存，供 connect callback 使用
            ws._idx   = idx

            def make_connect(b):
                def handle_connect(msg=None):
                    logger.info(f"WebSocket 連線成功，訂閱 {len(b)} 個合約...")
                    for sym in b:
                        ws.subscribe({'channel': 'trades', 'symbol': sym})
                    self.connected = True
                    self.error_message = None
                return handle_connect

            def handle_disconnect(*args):
                logger.warning("WebSocket 斷線，5 秒後重連...")
                self.connected = False
                time.sleep(5)
                self._reconnect()

            def handle_error(*args):
                msg = str(args)
                logger.error(f"WebSocket 錯誤：{msg}")
                self.error_message = msg

            def handle_message(raw):
                self._on_message(raw)

            ws.on('connect',    make_connect(batch))
            ws.on('disconnect', handle_disconnect)
            ws.on('error',      handle_error)
            ws.on('message',    handle_message)
            ws.connect()
            self.ws_list.append(ws)
            time.sleep(0.5)   # 避免短時間大量建立連線被擋

    # ── 處理推播 ───────────────────────────────────────────

    def _on_message(self, raw):
        """解析推播，更新 store，觸發 on_update callback"""
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return

        # 過濾系統事件（authenticated / subscribed / pong）
        if 'event' in data:
            return

        sym   = data.get('symbol')
        if not sym or sym not in self.store:
            return

        total = data.get('total', {})
        avg   = data.get('avgPrice')

        with self._lock:
            opt = self.store[sym]
            opt.trade_volume = int(total.get('tradeVolume') or 0)
            opt.bid_match    = int(total.get('totalBidMatch') or 0)
            opt.ask_match    = int(total.get('totalAskMatch') or 0)
            if avg:
                opt.avg_price = float(avg)

        self.last_updated = time.time()

        if self.on_update:
            try:
                self.on_update()
            except Exception as e:
                logger.error(f"on_update callback 錯誤：{e}")

    # ── 重連 ───────────────────────────────────────────────

    def _reconnect(self):
        logger.info("重新連線...")
        try:
            symbols = list(self.store.keys())
            self.ws_list.clear()
            self._subscribe_all(symbols)
        except Exception as e:
            logger.error(f"重連失敗：{e}，10 秒後再試...")
            time.sleep(10)
            self._reconnect()
