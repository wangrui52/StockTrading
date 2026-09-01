from dataclasses import dataclass
from datetime import date
from typing import Protocol


class MarketDataUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TradeCalendarRecord:
    trade_date: date
    is_open: bool


@dataclass(frozen=True, slots=True)
class StockRecord:
    market: str
    stock_code: str
    name: str
    industry: str | None
    list_date: date | None
    is_st: bool


@dataclass(frozen=True, slots=True)
class PriceRecord:
    market: str
    stock_code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    pct_change: float | None
    turnover_rate: float | None
    adjustment: str
    is_suspended: bool = False


@dataclass(frozen=True, slots=True)
class IndexRecord:
    index_code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    pct_change: float | None


class MarketDataGateway(Protocol):
    adapter_version: str

    def is_trade_date(self, value: date) -> bool: ...

    def trade_calendar(
        self, start_date: date, end_date: date
    ) -> list[TradeCalendarRecord]: ...

    def list_stocks(self) -> list[StockRecord]: ...

    def daily_prices(
        self, stock: StockRecord, end_date: date, *, start_date: date | None = None
    ) -> list[PriceRecord]: ...

    def index_prices(self, end_date: date) -> list[IndexRecord]: ...
