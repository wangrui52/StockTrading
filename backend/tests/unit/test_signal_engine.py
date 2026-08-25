from datetime import date, timedelta

import pytest

from app.domain.indicators import IndicatorSnapshot
from app.domain.market import MarketBar
from app.domain.signals import RiskLevel, SignalEngine, TrendSummary


def bar(
    day: int,
    close: float,
    *,
    high: float | None = None,
    pct_change: float | None = None,
) -> MarketBar:
    return MarketBar(
        trade_date=date(2025, 1, 1) + timedelta(days=day),
        close_qfq=close,
        high_qfq=high,
        volume=1_000,
        pct_change_raw=pct_change,
    )


def indicator(
    day: int,
    *,
    ma5: float | None = 10.0,
    ma20: float | None = 10.0,
    dif: float = 0.0,
    dea: float = 0.0,
    hist: float = 0.0,
    rsi: float | None = 60.0,
    volume_ratio: float | None = 1.0,
) -> IndicatorSnapshot:
    unavailable = frozenset(
        name for name, value in (("ma5", ma5), ("ma20", ma20), ("rsi14", rsi)) if value is None
    )
    return IndicatorSnapshot(
        trade_date=date(2025, 1, 1) + timedelta(days=day),
        ma5=ma5,
        ma10=None,
        ma20=ma20,
        ma60=None,
        dif=dif,
        dea=dea,
        macd_hist=hist,
        rsi14=rsi,
        boll_mid=None,
        boll_upper=None,
        boll_lower=None,
        volume_ratio_5_20=volume_ratio,
        unavailable=unavailable,
    )


def test_breakout_is_an_event_only_on_the_crossing_day() -> None:
    bars = [bar(0, 9.8), bar(1, 10.2), bar(2, 10.4)]
    indicators = [
        indicator(0, ma5=9.9, ma20=10.0, volume_ratio=1.0),
        indicator(1, ma5=10.1, ma20=10.0, volume_ratio=1.2),
        indicator(2, ma5=10.2, ma20=10.0, volume_ratio=1.3),
    ]

    result = SignalEngine().evaluate(bars, indicators)

    assert "BREAKOUT_MA20_WITH_VOLUME" in result[1].event_codes
    assert "BREAKOUT_MA20_WITH_VOLUME" not in result[2].event_codes
    assert {"PRICE_ABOVE_MA20", "MA5_ABOVE_MA20", "VOLUME_EXPANDED"}.issubset(result[2].state_codes)


def test_detects_macd_crosses_and_red_histogram_expansion() -> None:
    bars = [bar(0, 10.0), bar(1, 10.1), bar(2, 10.0)]
    indicators = [
        indicator(0, dif=-0.1, dea=0.0, hist=-0.2),
        indicator(1, dif=0.1, dea=0.0, hist=0.1),
        indicator(2, dif=-0.1, dea=0.0, hist=0.2),
    ]

    result = SignalEngine().evaluate(bars, indicators)

    assert "MACD_GOLDEN_CROSS" in result[1].event_codes
    assert "MACD_RED_EXPANDING" in result[2].state_codes
    assert "MACD_DEATH_CROSS" in result[2].event_codes


def test_rsi_entry_events_do_not_repeat_while_state_remains_true() -> None:
    bars = [bar(0, 10.0), bar(1, 10.1), bar(2, 10.2), bar(3, 10.3)]
    indicators = [
        indicator(0, rsi=45.0),
        indicator(1, rsi=55.0),
        indicator(2, rsi=70.0),
        indicator(3, rsi=81.0),
    ]

    result = SignalEngine().evaluate(bars, indicators)

    assert "RSI_ENTER_STRONG" in result[1].event_codes
    assert "RSI_ENTER_STRONG" not in result[2].event_codes
    assert "RSI_ENTER_OVERHEATED" in result[3].event_codes
    assert "RSI_OVERHEATED" in result[3].state_codes


def test_negative_event_sets_high_risk_even_when_positive_signal_also_exists() -> None:
    bars = [bar(0, 10.0), bar(1, 9.0, pct_change=-6.0)]
    indicators = [
        indicator(0, ma5=10.1, ma20=10.0, dif=-0.1, dea=0.0),
        indicator(1, ma5=9.1, ma20=9.5, dif=0.1, dea=0.0),
    ]

    result = SignalEngine().evaluate(bars, indicators)[-1]

    assert {"FALL_BELOW_MA20", "MACD_GOLDEN_CROSS", "DAILY_DROP"}.issubset(result.event_codes)
    assert result.risk_level is RiskLevel.HIGH


@pytest.mark.parametrize(
    ("close", "ma5", "ma20", "dif", "dea", "expected"),
    [
        (11.0, 10.5, 10.0, 0.2, 0.1, TrendSummary.BULLISH),
        (9.0, 9.5, 10.0, -0.2, -0.1, TrendSummary.BEARISH),
        (10.0, 10.0, 10.0, 0.0, 0.0, TrendSummary.NEUTRAL),
        (10.0, None, 10.0, 0.0, 0.0, TrendSummary.INSUFFICIENT_DATA),
    ],
)
def test_summarizes_trend_from_the_shared_indicator_snapshot(
    close: float,
    ma5: float | None,
    ma20: float,
    dif: float,
    dea: float,
    expected: TrendSummary,
) -> None:
    result = SignalEngine().evaluate(
        [bar(0, close)],
        [indicator(0, ma5=ma5, ma20=ma20, dif=dif, dea=dea)],
    )

    assert result[0].trend is expected


def test_marks_stock_near_previous_sixty_day_high_without_using_current_high() -> None:
    bars = [bar(day, 10.0, high=10.0) for day in range(60)] + [bar(60, 9.8, high=12.0)]
    indicators = [indicator(day) for day in range(61)]

    result = SignalEngine().evaluate(bars, indicators)

    assert "NEAR_60D_HIGH" in result[-1].state_codes


def test_zero_previous_high_does_not_create_signal_or_divide_by_zero() -> None:
    bars = [bar(day, 0.0, high=0.0) for day in range(61)]
    indicators = [indicator(day) for day in range(61)]

    result = SignalEngine().evaluate(bars, indicators)

    assert "NEAR_60D_HIGH" not in result[-1].state_codes


def test_rejects_indicator_snapshots_that_do_not_match_market_dates() -> None:
    with pytest.raises(ValueError, match="market bars and indicators must share trade dates"):
        SignalEngine().evaluate([bar(0, 10.0)], [indicator(1)])
