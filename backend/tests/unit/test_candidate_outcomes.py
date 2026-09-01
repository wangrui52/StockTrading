from dataclasses import replace
from datetime import date

import pytest

from app.domain.outcomes import (
    CompletedOutcome,
    OutcomeBar,
    UnavailableOutcome,
    calculate_outcome,
)


def sample_bars() -> list[OutcomeBar]:
    return [
        OutcomeBar(date(2026, 8, 28), 10.0, 11.0, 9.0, 10.5, False, 100),
        OutcomeBar(date(2026, 8, 31), 10.5, 12.0, 10.0, 11.5, False, 100),
        OutcomeBar(date(2026, 9, 1), 11.5, 13.0, 9.5, 12.0, False, 100),
        OutcomeBar(date(2026, 9, 2), 12.0, 13.0, 11.0, 12.5, False, 100),
        OutcomeBar(date(2026, 9, 3), 12.5, 14.0, 12.0, 13.0, False, 100),
    ]


def test_calculates_return_mfe_and_mae_from_next_open() -> None:
    bars = [
        OutcomeBar(
            trade_date=date(2026, 8, 24),
            open_raw=10.5,
            high_raw=11.0,
            low_raw=9.0,
            close_raw=10.5,
            is_suspended=False,
            volume=100,
        ),
        OutcomeBar(
            trade_date=date(2026, 8, 25),
            open_raw=10.5,
            high_raw=12.0,
            low_raw=10.0,
            close_raw=11.5,
            is_suspended=False,
            volume=100,
        ),
        OutcomeBar(
            trade_date=date(2026, 8, 26),
            open_raw=11.5,
            high_raw=13.0,
            low_raw=9.5,
            close_raw=12.6,
            is_suspended=False,
            volume=100,
        ),
    ]

    result = calculate_outcome(bars, horizon=3)

    assert isinstance(result, CompletedOutcome)
    assert result.reference_date == date(2026, 8, 24)
    assert result.evaluation_date == date(2026, 8, 26)
    assert result.reference_price == 10.5
    assert result.evaluation_price == 12.6
    assert result.return_rate == pytest.approx(20.0)
    assert result.mfe == pytest.approx((13 / 10.5 - 1) * 100)
    assert result.mae == pytest.approx((9 / 10.5 - 1) * 100)


@pytest.mark.parametrize(
    ("horizon", "evaluation_date", "return_rate", "mfe", "mae"),
    [
        (1, date(2026, 8, 28), 5.0, 10.0, -10.0),
        (3, date(2026, 9, 1), 20.0, 30.0, -10.0),
        (5, date(2026, 9, 3), 30.0, 40.0, -10.0),
    ],
)
def test_uses_ordered_trading_bars_for_each_horizon(
    horizon: int,
    evaluation_date: date,
    return_rate: float,
    mfe: float,
    mae: float,
) -> None:
    result = calculate_outcome(sample_bars(), horizon=horizon)  # type: ignore[arg-type]

    assert isinstance(result, CompletedOutcome)
    assert result.evaluation_date == evaluation_date
    assert result.return_rate == pytest.approx(return_rate)
    assert result.mfe == pytest.approx(mfe)
    assert result.mae == pytest.approx(mae)


def test_returns_not_due_when_the_horizon_has_insufficient_bars() -> None:
    result = calculate_outcome(sample_bars()[:2], horizon=3)

    assert result == UnavailableOutcome(reason_code="NOT_DUE")


@pytest.mark.parametrize(
    "reference_bar",
    [
        replace(sample_bars()[0], is_suspended=True),
        replace(sample_bars()[0], volume=0),
        replace(sample_bars()[0], open_raw=0),
        replace(sample_bars()[0], open_raw=float("nan")),
    ],
)
def test_returns_reference_unavailable_when_next_open_cannot_be_traded(
    reference_bar: OutcomeBar,
) -> None:
    bars = [reference_bar, *sample_bars()[1:]]

    result = calculate_outcome(bars, horizon=1)

    assert result == UnavailableOutcome(reason_code="REFERENCE_UNAVAILABLE")


@pytest.mark.parametrize(
    ("horizon", "evaluation_bar"),
    [
        (3, replace(sample_bars()[2], is_suspended=True)),
        (3, replace(sample_bars()[2], volume=0)),
        (5, replace(sample_bars()[4], is_suspended=True)),
        (5, replace(sample_bars()[4], volume=0)),
    ],
)
def test_returns_evaluation_unavailable_when_horizon_close_cannot_be_traded(
    horizon: int,
    evaluation_bar: OutcomeBar,
) -> None:
    bars = sample_bars()
    bars[horizon - 1] = evaluation_bar

    result = calculate_outcome(bars, horizon=horizon)  # type: ignore[arg-type]

    assert result == UnavailableOutcome(reason_code="EVALUATION_UNAVAILABLE")


@pytest.mark.parametrize(
    "intermediate_bar",
    [
        replace(sample_bars()[1], high_raw=100.0, low_raw=0.1, is_suspended=True),
        replace(sample_bars()[1], high_raw=100.0, low_raw=0.1, volume=0),
    ],
)
def test_excludes_an_untradable_intermediate_bar_from_mfe_and_mae(
    intermediate_bar: OutcomeBar,
) -> None:
    bars = sample_bars()
    bars[1] = intermediate_bar

    result = calculate_outcome(bars, horizon=3)

    assert isinstance(result, CompletedOutcome)
    assert result.mfe == pytest.approx(30.0)
    assert result.mae == pytest.approx(-10.0)


def test_returns_invalid_price_data_for_malformed_evaluation_ohlc() -> None:
    bars = sample_bars()
    bars[2] = replace(bars[2], close_raw=float("nan"))

    result = calculate_outcome(bars, horizon=3)

    assert result == UnavailableOutcome(reason_code="INVALID_PRICE_DATA")


@pytest.mark.parametrize(
    "invalid_bar",
    [
        replace(sample_bars()[1], open_raw=float("inf")),
        replace(sample_bars()[1], high_raw=float("nan")),
        replace(sample_bars()[1], low_raw=0),
        replace(sample_bars()[1], close_raw=-1),
        replace(sample_bars()[1], high_raw=11.0),
        replace(sample_bars()[1], low_raw=11.0),
    ],
)
def test_returns_invalid_price_data_for_malformed_ohlc(
    invalid_bar: OutcomeBar,
) -> None:
    bars = sample_bars()
    bars[1] = invalid_bar

    result = calculate_outcome(bars, horizon=3)

    assert result == UnavailableOutcome(reason_code="INVALID_PRICE_DATA")


@pytest.mark.parametrize("horizon", [2, True, False, 1.0, 3.0])
def test_rejects_an_unsupported_horizon(horizon: object) -> None:
    with pytest.raises(ValueError, match="horizon"):
        calculate_outcome(sample_bars(), horizon=horizon)  # type: ignore[arg-type]


@pytest.mark.parametrize("second_date", [date(2026, 8, 28), date(2026, 8, 27)])
def test_requires_dates_to_be_strictly_ascending(second_date: date) -> None:
    bars = sample_bars()
    bars[1] = replace(bars[1], trade_date=second_date)

    with pytest.raises(ValueError, match="strictly ascending"):
        calculate_outcome(bars, horizon=1)
