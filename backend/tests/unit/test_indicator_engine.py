from datetime import date, timedelta

import pytest

from app.domain.indicators import IndicatorEngine
from app.domain.market import MarketBar


def make_bars(closes: list[float], volumes: list[int] | None = None) -> list[MarketBar]:
    first_day = date(2025, 1, 1)
    actual_volumes = volumes or [1_000] * len(closes)
    return [
        MarketBar(
            trade_date=first_day + timedelta(days=index),
            close_qfq=close,
            volume=actual_volumes[index],
        )
        for index, close in enumerate(closes)
    ]


def test_calculates_simple_moving_averages_with_expected_windows() -> None:
    result = IndicatorEngine().calculate(make_bars([float(value) for value in range(1, 61)]))

    assert result[-1].ma5 == pytest.approx(58.0)
    assert result[-1].ma10 == pytest.approx(55.5)
    assert result[-1].ma20 == pytest.approx(50.5)
    assert result[-1].ma60 == pytest.approx(30.5)
    assert result[58].ma60 is None
    assert "ma60" in result[58].unavailable


def test_macd_uses_first_close_as_ema_seed_and_double_histogram() -> None:
    result = IndicatorEngine().calculate(make_bars([1.0, 2.0, 3.0]))

    assert result[0].dif == pytest.approx(0.0)
    assert result[0].dea == pytest.approx(0.0)
    assert result[2].dif == pytest.approx(0.22113456871291648)
    assert result[2].dea == pytest.approx(0.056990446506116066)
    assert result[2].macd_hist == pytest.approx(0.3282882444136008)


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        ([float(value) for value in range(1, 16)], 100.0),
        ([float(value) for value in range(15, 0, -1)], 0.0),
        ([10.0] * 15, 50.0),
    ],
)
def test_rsi14_uses_wilder_seed_and_handles_zero_gain_or_loss(
    closes: list[float], expected: float
) -> None:
    result = IndicatorEngine().calculate(make_bars(closes))

    assert result[13].rsi14 is None
    assert result[14].rsi14 == pytest.approx(expected)


def test_bollinger_uses_population_standard_deviation() -> None:
    result = IndicatorEngine().calculate(make_bars([float(value) for value in range(1, 21)]))

    assert result[-1].boll_mid == pytest.approx(10.5)
    assert result[-1].boll_upper == pytest.approx(22.032562594670797)
    assert result[-1].boll_lower == pytest.approx(-1.0325625946707966)


def test_volume_ratio_compares_five_day_and_twenty_day_averages() -> None:
    closes = [10.0] * 20
    volumes = list(range(1, 21))

    result = IndicatorEngine().calculate(make_bars(closes, volumes))

    assert result[-1].volume_ratio_5_20 == pytest.approx(18.0 / 10.5)


def test_does_not_invent_volume_ratio_when_twenty_day_average_is_zero() -> None:
    result = IndicatorEngine().calculate(make_bars([10.0] * 20, [0] * 20))

    assert result[-1].volume_ratio_5_20 is None
    assert "volume_ratio_5_20" in result[-1].unavailable


def test_rejects_bars_that_are_not_strictly_ordered_by_trade_date() -> None:
    bars = make_bars([10.0, 11.0])
    reversed_bars = list(reversed(bars))

    with pytest.raises(ValueError, match="trade_date must be strictly increasing"):
        IndicatorEngine().calculate(reversed_bars)
