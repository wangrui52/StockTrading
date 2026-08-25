from dataclasses import dataclass

from app.domain.indicators import IndicatorSnapshot
from app.domain.market import MarketBar
from app.domain.signals import SignalEvaluation


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    matched: bool
    score: float
    reasons: frozenset[str]


class CandidateEngine:
    """PRD 默认候选方案的唯一领域实现。"""

    def evaluate(
        self,
        *,
        is_st: bool,
        bars: list[MarketBar],
        indicators: list[IndicatorSnapshot],
        evaluations: list[SignalEvaluation],
    ) -> CandidateDecision:
        if (
            is_st
            or len(bars) < 120
            or len(bars) != len(indicators)
            or len(bars) != len(evaluations)
            or bars[-1].is_suspended
        ):
            return CandidateDecision(False, 0, frozenset())
        current_bar = bars[-1]
        current = indicators[-1]
        if (
            current.ma5 is None
            or current.ma20 is None
            or current.rsi14 is None
            or current_bar.close_qfq <= current.ma20
            or current.ma5 <= current.ma20
            or not 45 <= current.rsi14 <= 75
        ):
            return CandidateDecision(False, 0, frozenset())
        recent_events = set().union(*(item.event_codes for item in evaluations[-3:]))
        matched_events = recent_events & {
            "BREAKOUT_MA20_WITH_VOLUME",
            "MACD_GOLDEN_CROSS",
        }
        if not matched_events:
            return CandidateDecision(False, 0, frozenset())
        reasons = {
            "PRICE_ABOVE_MA20",
            "MA5_ABOVE_MA20",
            "RSI_IN_CANDIDATE_RANGE",
            *matched_events,
        }
        return CandidateDecision(True, float(len(reasons)), frozenset(reasons))
