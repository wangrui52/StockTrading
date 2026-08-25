from datetime import date, timedelta
from typing import Any

from app.ports.market_data import IndexRecord, MarketDataUnavailable, PriceRecord, StockRecord


class AkShareMarketDataGateway:
    """把易变化的 AkShare DataFrame 隔离为稳定领域记录。"""

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            import akshare

            client = akshare
        self.client = client
        self.adapter_version = f"akshare-{getattr(client, '__version__', 'unknown')}"

    def is_trade_date(self, value: date) -> bool:
        try:
            frame = self.client.tool_trade_date_hist_sina()
            return value in {self._date(item) for item in frame["trade_date"].tolist()}
        except Exception as error:
            raise MarketDataUnavailable("AkShare 交易日历获取失败") from error

    def list_stocks(self) -> list[StockRecord]:
        try:
            frame = self.client.stock_info_a_code_name()
            code_column = "code" if "code" in frame.columns else "代码"
            name_column = "name" if "name" in frame.columns else "名称"
            stocks: list[StockRecord] = []
            for row in frame.to_dict("records"):
                code = str(row[code_column]).zfill(6)
                market = self._market(code)
                if market is None:
                    continue
                name = str(row[name_column])
                stocks.append(
                    StockRecord(
                        market=market,
                        stock_code=code,
                        name=name,
                        industry=None,
                        list_date=None,
                        is_st="ST" in name.upper(),
                    )
                )
            return stocks
        except Exception as error:
            raise MarketDataUnavailable("AkShare 股票池获取失败") from error

    def daily_prices(
        self, stock: StockRecord, end_date: date, *, start_date: date | None = None
    ) -> list[PriceRecord]:
        # 多取一年后截取最近 750 个交易日，覆盖节假日导致三年样本不足的情况。
        requested_start = start_date or end_date - timedelta(days=4 * 366)
        records: list[PriceRecord] = []
        try:
            for adjustment, source_adjustment in (("raw", ""), ("qfq", "qfq")):
                frame = self.client.stock_zh_a_hist(
                    symbol=stock.stock_code,
                    period="daily",
                    start_date=requested_start.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    adjust=source_adjustment,
                )
                if start_date is None:
                    frame = frame.tail(750)
                records.extend(
                    self._price_record(stock, row, adjustment) for row in frame.to_dict("records")
                )
        except Exception as error:
            raise MarketDataUnavailable(f"AkShare 个股行情获取失败: {stock.stock_code}") from error
        order = {"raw": 0, "qfq": 1}
        return sorted(records, key=lambda item: (item.trade_date, order[item.adjustment]))

    def index_prices(self, end_date: date) -> list[IndexRecord]:
        symbols = {
            "000001": "sh000001",
            "399001": "sz399001",
            "399006": "sz399006",
            "899050": "bj899050",
        }
        records: list[IndexRecord] = []
        errors: list[Exception] = []
        for index_code, symbol in symbols.items():
            try:
                frame = self.client.stock_zh_index_daily_em(
                    symbol=symbol,
                    start_date=(end_date - timedelta(days=10)).strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                )
                rows = [
                    row for row in frame.to_dict("records") if self._date(row["date"]) <= end_date
                ]
                if not rows:
                    continue
                current = rows[-1]
                previous_close = float(rows[-2]["close"]) if len(rows) > 1 else None
                close = float(current["close"])
                pct_change = (
                    (close - previous_close) / previous_close * 100
                    if previous_close not in (None, 0)
                    else None
                )
                records.append(
                    IndexRecord(
                        index_code=index_code,
                        trade_date=self._date(current["date"]),
                        open=float(current["open"]),
                        high=float(current["high"]),
                        low=float(current["low"]),
                        close=close,
                        pct_change=pct_change,
                    )
                )
            except Exception as error:
                errors.append(error)
        if not records and errors:
            raise MarketDataUnavailable("AkShare 指数行情获取失败") from errors[0]
        return records

    @staticmethod
    def _price_record(stock: StockRecord, row: dict[str, Any], adjustment: str) -> PriceRecord:
        return PriceRecord(
            market=stock.market,
            stock_code=stock.stock_code,
            trade_date=AkShareMarketDataGateway._date(row["日期"]),
            open=float(row["开盘"]),
            high=float(row["最高"]),
            low=float(row["最低"]),
            close=float(row["收盘"]),
            volume=int(row["成交量"]) * 100,
            amount=float(row["成交额"]),
            pct_change=AkShareMarketDataGateway._optional_float(row.get("涨跌幅")),
            turnover_rate=AkShareMarketDataGateway._optional_float(row.get("换手率")),
            adjustment=adjustment,
        )

    @staticmethod
    def _market(code: str) -> str | None:
        if code.startswith(("4", "8", "92")):
            return "BJ"
        if code.startswith(("0", "3")):
            return "SZ"
        if code.startswith(("6", "68")):
            return "SH"
        return None

    @staticmethod
    def _date(value: Any) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            if value != value:
                return None
        except TypeError:
            return None
        return float(value)
