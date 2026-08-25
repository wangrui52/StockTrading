from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class MarketBar:
    trade_date: date
    close_qfq: float
    volume: int
    high_qfq: float | None = None
    pct_change_raw: float | None = None
