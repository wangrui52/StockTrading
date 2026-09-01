from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.ports.market_data import StockRecord


@dataclass(frozen=True, slots=True)
class RealtimeQuote:
    market: str
    stock_code: str
    stock_name: str
    latest_price: float | None
    pct_change: float | None
    volume: int
    amount: float
    quoted_at: datetime


class RealtimeGateway(Protocol):
    source: str

    def list_stocks(self) -> list[StockRecord]: ...

    def quotes(self, symbols: list[str]) -> list[RealtimeQuote]: ...
