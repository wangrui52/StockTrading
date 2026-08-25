from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.domain.indicators import IndicatorSnapshot
from app.domain.market import MarketBar


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TrendSummary(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class SignalEvaluation:
    trade_date: date
    rule_version: str
    state_codes: frozenset[str]
    event_codes: frozenset[str]
    trend: TrendSummary
    risk_level: RiskLevel


class SignalEngine:
    """把统一行情与指标转换为可复现的状态和跨日事件。"""

    def evaluate(
        self,
        bars: list[MarketBar],
        indicators: list[IndicatorSnapshot],
        rule_version: str = "v1",
    ) -> list[SignalEvaluation]:
        if len(bars) != len(indicators) or any(
            bar.trade_date != snapshot.trade_date
            for bar, snapshot in zip(bars, indicators, strict=False)
        ):
            raise ValueError("market bars and indicators must share trade dates")

        evaluations: list[SignalEvaluation] = []
        for index, (current_bar, current) in enumerate(
            zip(bars, indicators, strict=False)
        ):
            previous_bar = bars[index - 1] if index > 0 else None
            previous = indicators[index - 1] if index > 0 else None
            state_codes = self._states(bars, index, current_bar, current, previous)
            event_codes = self._events(current_bar, current, previous_bar, previous)
            trend = self._trend(current_bar, current)
            risk = self._risk(state_codes, event_codes, current)
            evaluations.append(
                SignalEvaluation(
                    trade_date=current_bar.trade_date,
                    rule_version=rule_version,
                    state_codes=frozenset(state_codes),
                    event_codes=frozenset(event_codes),
                    trend=trend,
                    risk_level=risk,
                )
            )
        return evaluations

    @staticmethod
    def _states(
        bars: list[MarketBar],
        index: int,
        current_bar: MarketBar,
        current: IndicatorSnapshot,
        previous: IndicatorSnapshot | None,
    ) -> set[str]:
        states: set[str] = set()
        if current.ma20 is not None and current_bar.close_qfq > current.ma20:
            states.add("PRICE_ABOVE_MA20")
        if current.ma5 is not None and current.ma20 is not None and current.ma5 > current.ma20:
            states.add("MA5_ABOVE_MA20")
        if current.volume_ratio_5_20 is not None and current.volume_ratio_5_20 >= 1.2:
            states.add("VOLUME_EXPANDED")
        if (
            previous is not None
            and current.macd_hist > previous.macd_hist
            and previous.macd_hist > 0
        ):
            states.add("MACD_RED_EXPANDING")
        if current.rsi14 is not None and 50 <= current.rsi14 <= 75:
            states.add("RSI_STRONG")
        if current.rsi14 is not None and current.rsi14 > 80:
            states.add("RSI_OVERHEATED")
        if index >= 60:
            previous_high = max(
                bar.high_qfq if bar.high_qfq is not None else bar.close_qfq
                for bar in bars[index - 60 : index]
            )
            if previous_high > 0:
                distance = (previous_high - current_bar.close_qfq) / previous_high
                if 0 <= distance <= 0.03:
                    states.add("NEAR_60D_HIGH")
        return states

    @staticmethod
    def _events(
        current_bar: MarketBar,
        current: IndicatorSnapshot,
        previous_bar: MarketBar | None,
        previous: IndicatorSnapshot | None,
    ) -> set[str]:
        events: set[str] = set()
        if previous_bar is not None and previous is not None:
            if (
                previous.ma20 is not None
                and current.ma20 is not None
                and current.volume_ratio_5_20 is not None
                and previous_bar.close_qfq <= previous.ma20
                and current_bar.close_qfq > current.ma20
                and current.volume_ratio_5_20 >= 1.2
            ):
                events.add("BREAKOUT_MA20_WITH_VOLUME")
            if (
                previous.ma20 is not None
                and current.ma20 is not None
                and previous_bar.close_qfq >= previous.ma20
                and current_bar.close_qfq < current.ma20
            ):
                events.add("FALL_BELOW_MA20")
            if previous.dif <= previous.dea and current.dif > current.dea:
                events.add("MACD_GOLDEN_CROSS")
            if previous.dif >= previous.dea and current.dif < current.dea:
                events.add("MACD_DEATH_CROSS")
            if (
                previous.rsi14 is not None
                and current.rsi14 is not None
                and not 50 <= previous.rsi14 <= 75
                and 50 <= current.rsi14 <= 75
            ):
                events.add("RSI_ENTER_STRONG")
            if (
                previous.rsi14 is not None
                and current.rsi14 is not None
                and previous.rsi14 <= 80
                and current.rsi14 > 80
            ):
                events.add("RSI_ENTER_OVERHEATED")
        if current_bar.pct_change_raw is not None and current_bar.pct_change_raw >= 5:
            events.add("DAILY_SURGE")
        if current_bar.pct_change_raw is not None and current_bar.pct_change_raw <= -5:
            events.add("DAILY_DROP")
        return events

    @staticmethod
    def _trend(current_bar: MarketBar, current: IndicatorSnapshot) -> TrendSummary:
        if current.ma5 is None or current.ma20 is None:
            return TrendSummary.INSUFFICIENT_DATA
        if (
            current_bar.close_qfq > current.ma20
            and current.ma5 > current.ma20
            and current.dif > current.dea
        ):
            return TrendSummary.BULLISH
        if (
            current_bar.close_qfq < current.ma20
            and current.ma5 < current.ma20
            and current.dif < current.dea
        ):
            return TrendSummary.BEARISH
        return TrendSummary.NEUTRAL

    @staticmethod
    def _risk(
        state_codes: set[str],
        event_codes: set[str],
        current: IndicatorSnapshot,
    ) -> RiskLevel:
        if event_codes & {"FALL_BELOW_MA20", "MACD_DEATH_CROSS", "DAILY_DROP"}:
            return RiskLevel.HIGH
        critical_unavailable = {"ma5", "ma20", "rsi14"}
        if "RSI_OVERHEATED" in state_codes or current.unavailable & critical_unavailable:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
