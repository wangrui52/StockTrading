from dataclasses import dataclass, field
from datetime import date

from app.ports.market_data import MarketDataUnavailable, PriceRecord, StockRecord


@dataclass(slots=True)
class FakeMarketDataGateway:
    open_dates: set[date]
    stocks: list[StockRecord]
    histories: dict[str, list[PriceRecord]]
    failed_codes: set[str] = field(default_factory=set)
    adapter_version: str = "fake-v1"

    def is_trade_date(self, value: date) -> bool:
        return value in self.open_dates

    def list_stocks(self) -> list[StockRecord]:
        return self.stocks

    def daily_prices(self, stock: StockRecord, end_date: date) -> list[PriceRecord]:
        if stock.stock_code in self.failed_codes:
            raise MarketDataUnavailable(f"failed to fetch {stock.stock_code}")
        return [
            item for item in self.histories.get(stock.stock_code, []) if item.trade_date <= end_date
        ]
