from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.candidates import CandidateEngine
from app.domain.indicators import IndicatorSnapshot
from app.domain.market import MarketBar
from app.domain.signals import RiskLevel, SignalEvaluation, TrendSummary


def eligible_sample() -> tuple[list[MarketBar], list[IndicatorSnapshot], list[SignalEvaluation]]:
    target = date(2025, 3, 31)
    bars = [
        MarketBar(target - timedelta(days=119 - index), 10 + index * 0.01, 100_000)
        for index in range(120)
    ]
    indicators = [
        IndicatorSnapshot(
            trade_date=bar.trade_date,
            ma5=10,
            ma10=10,
            ma20=9,
            ma60=8,
            dif=0.2,
            dea=0.1,
            macd_hist=0.2,
            rsi14=60,
            boll_mid=9,
            boll_upper=11,
            boll_lower=7,
            volume_ratio_5_20=1.3,
            unavailable=frozenset(),
        )
        for bar in bars
    ]
    evaluations = [
        SignalEvaluation(
            trade_date=bar.trade_date,
            rule_version="v1",
            state_codes=frozenset({"PRICE_ABOVE_MA20", "MA5_ABOVE_MA20"}),
            event_codes=frozenset({"MACD_GOLDEN_CROSS"}) if index == 118 else frozenset(),
            trend=TrendSummary.BULLISH,
            risk_level=RiskLevel.LOW,
        )
        for index, bar in enumerate(bars)
    ]
    return bars, indicators, evaluations


def test_default_candidate_requires_all_prd_conditions() -> None:
    bars, indicators, evaluations = eligible_sample()

    result = CandidateEngine().evaluate(
        is_st=False,
        bars=bars,
        indicators=indicators,
        evaluations=evaluations,
    )

    assert result.matched
    assert "MACD_GOLDEN_CROSS" in result.reasons


@pytest.mark.parametrize("case", ["st", "short_history", "suspended", "rsi", "no_event"])
def test_default_candidate_rejects_each_exclusion(case: str) -> None:
    bars, indicators, evaluations = eligible_sample()
    is_st = case == "st"
    if case == "short_history":
        bars, indicators, evaluations = bars[-119:], indicators[-119:], evaluations[-119:]
    if case == "suspended":
        bars[-1] = replace(bars[-1], is_suspended=True)
    if case == "rsi":
        indicators[-1] = replace(indicators[-1], rsi14=76)
    if case == "no_event":
        evaluations = [replace(item, event_codes=frozenset()) for item in evaluations]

    assert (
        not CandidateEngine()
        .evaluate(
            is_st=is_st,
            bars=bars,
            indicators=indicators,
            evaluations=evaluations,
        )
        .matched
    )
