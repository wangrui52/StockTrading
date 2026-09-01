from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Literal

type OutcomeHorizon = Literal[1, 3, 5]
type UnavailableReason = Literal[
    "NOT_DUE",
    "REFERENCE_UNAVAILABLE",
    "EVALUATION_UNAVAILABLE",
    "INVALID_PRICE_DATA",
]


@dataclass(frozen=True, slots=True)
class OutcomeBar:
    trade_date: date
    open_raw: float
    high_raw: float
    low_raw: float
    close_raw: float
    is_suspended: bool
    volume: int


@dataclass(frozen=True, slots=True)
class CompletedOutcome:
    reference_date: date
    evaluation_date: date
    reference_price: float
    evaluation_price: float
    return_rate: float
    mfe: float
    mae: float


@dataclass(frozen=True, slots=True)
class UnavailableOutcome:
    reason_code: UnavailableReason


type OutcomeResult = CompletedOutcome | UnavailableOutcome


def _has_valid_ohlc(bar: OutcomeBar) -> bool:
    prices = (bar.open_raw, bar.high_raw, bar.low_raw, bar.close_raw)
    return (
        all(isfinite(price) and price > 0 for price in prices)
        and bar.high_raw >= max(bar.open_raw, bar.close_raw)
        and bar.low_raw <= min(bar.open_raw, bar.close_raw)
        and bar.high_raw >= bar.low_raw
    )


def calculate_outcome(
    bars: Sequence[OutcomeBar], horizon: OutcomeHorizon
) -> OutcomeResult:
    """计算 T 之后有效交易日序列的候选表现。"""
    if type(horizon) is not int or horizon not in (1, 3, 5):
        raise ValueError("horizon must be one of 1, 3, 5")
    if any(
        current.trade_date >= following.trade_date
        for current, following in zip(bars, bars[1:], strict=False)
    ):
        raise ValueError("bar dates must be strictly ascending")
    if len(bars) < horizon:
        return UnavailableOutcome(reason_code="NOT_DUE")
    window = bars[:horizon]
    reference = window[0]
    evaluation = window[-1]
    if (
        reference.is_suspended
        or reference.volume <= 0
        or not isfinite(reference.open_raw)
        or reference.open_raw <= 0
    ):
        return UnavailableOutcome(reason_code="REFERENCE_UNAVAILABLE")
    if evaluation.is_suspended or evaluation.volume <= 0:
        return UnavailableOutcome(reason_code="EVALUATION_UNAVAILABLE")
    tradable_window = [
        bar for bar in window if not bar.is_suspended and bar.volume > 0
    ]
    if any(not _has_valid_ohlc(bar) for bar in tradable_window):
        return UnavailableOutcome(reason_code="INVALID_PRICE_DATA")
    return CompletedOutcome(
        reference_date=reference.trade_date,
        evaluation_date=evaluation.trade_date,
        reference_price=reference.open_raw,
        evaluation_price=evaluation.close_raw,
        return_rate=(evaluation.close_raw / reference.open_raw - 1) * 100,
        mfe=(max(bar.high_raw for bar in tradable_window) / reference.open_raw - 1)
        * 100,
        mae=(min(bar.low_raw for bar in tradable_window) / reference.open_raw - 1)
        * 100,
    )
