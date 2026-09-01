from dataclasses import dataclass, field
from datetime import date, timedelta

from app.ports.market_data import (
    IndexRecord,
    MarketDataUnavailable,
    PriceRecord,
    StockRecord,
    TradeCalendarRecord,
)


@dataclass(slots=True)
class FakeMarketDataGateway:
    open_dates: set[date]
    stocks: list[StockRecord]
    histories: dict[str, list[PriceRecord]]
    indices: list[IndexRecord] = field(default_factory=list)
    failed_codes: set[str] = field(default_factory=set)
    requested_start_dates: list[date | None] = field(default_factory=list)
    adapter_version: str = "fake-v1"

    def is_trade_date(self, value: date) -> bool:
        return value in self.open_dates

    def trade_calendar(
        self, start_date: date, end_date: date
    ) -> list[TradeCalendarRecord]:
        dates = (
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
        )
        return [
            TradeCalendarRecord(value, value in self.open_dates)
            for value in dates
        ]

    def list_stocks(self) -> list[StockRecord]:
        return self.stocks

    def daily_prices(
        self, stock: StockRecord, end_date: date, *, start_date: date | None = None
    ) -> list[PriceRecord]:
        self.requested_start_dates.append(start_date)
        if stock.stock_code in self.failed_codes:
            raise MarketDataUnavailable(f"failed to fetch {stock.stock_code}")
        return [
            item
            for item in self.histories.get(stock.stock_code, [])
            if item.trade_date <= end_date and (start_date is None or item.trade_date >= start_date)
        ]

    def index_prices(self, end_date: date) -> list[IndexRecord]:
        return [item for item in self.indices if item.trade_date <= end_date]
