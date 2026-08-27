"""公开日线：新浪完整股票池、腾讯 OHLCV、AkShare 新浪交易日历。"""

from datetime import date, datetime
from math import ceil, isfinite
from typing import Any
from zoneinfo import ZoneInfo

import requests

from app.adapters.akshare_market_data import AkShareMarketDataGateway
from app.ports.market_data import IndexRecord, MarketDataUnavailable, PriceRecord, StockRecord


class TencentMarketDataGateway(AkShareMarketDataGateway):
    def __init__(self, client: Any | None = None, *, get: Any = requests.get) -> None:
        super().__init__(client)
        self.adapter_version = "tencent-sina-v1"
        self.get = get
        self._custom_calendar = client is not None

    def _trade_dates(self) -> set[date]:
        if self._custom_calendar:
            return super()._trade_dates()
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if self._calendar_day != today:
            try:
                import pandas as pd
                from akshare.stock.cons import hk_js_decode
                from py_mini_racer import MiniRacer

                response = self.get(
                    "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt", timeout=15
                )
                response.raise_for_status()
                encoded = response.text.split("=")[1].split(";")[0].replace('"', "")
                with MiniRacer() as decoder:
                    decoder.eval(hk_js_decode)
                    values = decoder.call("d", encoded)
                dates = set(pd.to_datetime(values).date)
                if not dates:
                    raise ValueError("交易日历为空")
                dates.add(date(1992, 5, 4))
                self._calendar, self._calendar_day = dates, today
            except Exception as error:
                raise MarketDataUnavailable(f"新浪交易日历获取失败：{error}") from error
        return self._calendar

    def _json(self, url: str, params: dict[str, Any]) -> Any:
        response = self.get(url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()

    def list_stocks(self) -> list[StockRecord]:
        base = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        try:
            count = int(self._json(base + "Market_Center.getHQNodeStockCount", {"node": "hs_a"}))
            stocks: dict[str, StockRecord] = {}
            for page in range(1, ceil(count / 100) + 1):
                rows = self._json(
                    base + "Market_Center.getHQNodeData",
                    {
                        "page": page,
                        "num": 100,
                        "sort": "symbol",
                        "asc": 1,
                        "node": "hs_a",
                        "symbol": "",
                        "_s_r_a": "page",
                    },
                )
                for row in rows:
                    symbol = row["symbol"]
                    market, code, name = symbol[:2].upper(), row["code"], row["name"]
                    if market in {"SH", "SZ", "BJ"}:
                        stocks[symbol] = StockRecord(market, code, name, None, None, "ST" in name)
            if count <= 0 or len(stocks) != count:
                raise ValueError(f"股票池分页不完整：应有 {count}，实际 {len(stocks)}")
            return list(stocks.values())
        except Exception as error:
            raise MarketDataUnavailable(f"新浪股票池获取失败：{error}") from error

    def _history(
        self, symbol: str, adjustment: str, count: int
    ) -> tuple[list[list[Any]], list[Any]]:
        body = self._json(
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
            {"param": f"{symbol},day,,,{count},{adjustment}"},
        )
        if body.get("code") != 0:
            raise ValueError("腾讯日线响应失败")
        data = body["data"][symbol]
        # 无复权事件时腾讯以 day 返回，与 qfqday 含义相同。
        rows = data.get(adjustment + "day", data.get("day", []))
        if not rows:
            raise ValueError("日线为空")
        return sorted(rows, key=lambda row: row[0]), data.get("qt", {}).get(symbol, [])

    def daily_prices(
        self, stock: StockRecord, end_date: date, *, start_date: date | None = None
    ) -> list[PriceRecord]:
        symbol = stock.market.lower() + stock.stock_code
        count = (
            750 if start_date is None else min(750, max(20, (date.today() - start_date).days + 5))
        )
        try:
            raw, quote = self._history(symbol, "", count)
            qfq, _ = self._history(symbol, "qfq", count)
            raw_changes: dict[str, float | None] = {}
            # 历史相邻未复权收盘价不等于交易所涨跌幅，除权日尤其不能推算。
            if len(quote) > 32:
                for row in raw:
                    if row[0].replace("-", "") == str(quote[30])[:8] and float(row[2]) == float(
                        quote[3]
                    ):
                        raw_changes[row[0]] = self._optional_float(quote[32])
            records: list[PriceRecord] = []
            for adjustment, rows in (("raw", raw), ("qfq", qfq)):
                for row in rows:
                    trade_date = date.fromisoformat(row[0])
                    if trade_date > end_date or (start_date and trade_date < start_date):
                        continue
                    # 腾讯科创板成交量为股；沪深主板、创业板和北交所为手。
                    volume = float(row[5]) * (1 if symbol.startswith("sh68") else 100)
                    values = [float(row[i]) for i in (1, 2, 3, 4, 8)] + [volume]
                    if not all(isfinite(value) and value >= 0 for value in values):
                        raise ValueError("无效行情数字")
                    records.append(
                        PriceRecord(
                            market=stock.market,
                            stock_code=stock.stock_code,
                            trade_date=trade_date,
                            open=float(row[1]),
                            close=float(row[2]),
                            high=float(row[3]),
                            low=float(row[4]),
                            volume=round(volume),
                            amount=float(row[8]) * 10_000,
                            pct_change=raw_changes.get(row[0]),
                            turnover_rate=self._optional_float(row[7]),
                            adjustment=adjustment,
                            is_suspended=volume == 0,
                        )
                    )
            for adjustment in ("raw", "qfq"):
                if not any(
                    r.trade_date == end_date and r.adjustment == adjustment for r in records
                ):
                    raise ValueError(f"最新日线尚未到达 {end_date}（{adjustment}）")
            return sorted(records, key=lambda r: (r.trade_date, r.adjustment == "qfq"))
        except Exception as error:
            raise MarketDataUnavailable(f"腾讯 {symbol} 最新日线获取失败：{error}") from error

    def index_prices(self, end_date: date) -> list[IndexRecord]:
        records = []
        for code, symbol in (
            ("000001", "sh000001"),
            ("399001", "sz399001"),
            ("399006", "sz399006"),
            ("899050", "bj899050"),
        ):
            try:
                history, _ = self._history(symbol, "", 20)
                rows = [r for r in history if date.fromisoformat(r[0]) <= end_date]
                if not rows or rows[-1][0] != end_date.isoformat():
                    continue
                row = rows[-1]
                prior = float(rows[-2][2]) if len(rows) > 1 else None
                records.append(
                    IndexRecord(
                        index_code=code,
                        trade_date=end_date,
                        open=float(row[1]),
                        close=float(row[2]),
                        high=float(row[3]),
                        low=float(row[4]),
                        pct_change=(float(row[2]) / prior - 1) * 100 if prior else None,
                    )
                )
            except (ValueError, KeyError, requests.RequestException):
                continue
        if not records:
            raise MarketDataUnavailable("腾讯最新指数日线获取失败")
        return records
