from dataclasses import dataclass
from datetime import date
from math import sqrt

from app.domain.market import MarketBar


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    trade_date: date
    ma5: float | None
    ma10: float | None
    ma20: float | None
    ma60: float | None
    dif: float
    dea: float
    macd_hist: float
    rsi14: float | None
    boll_mid: float | None
    boll_upper: float | None
    boll_lower: float | None
    volume_ratio_5_20: float | None
    unavailable: frozenset[str]


class IndicatorEngine:
    """依据 PRD 的固定公式生成统一指标快照。"""

    def calculate(self, bars: list[MarketBar]) -> list[IndicatorSnapshot]:
        if not bars:
            return []
        if any(
            current.trade_date <= previous.trade_date
            for previous, current in zip(bars, bars[1:], strict=False)
        ):
            raise ValueError("trade_date must be strictly increasing")

        closes = [bar.close_qfq for bar in bars]
        volumes = [bar.volume for bar in bars]
        ema12 = closes[0]
        ema26 = closes[0]
        dea = 0.0
        average_gain: float | None = None
        average_loss: float | None = None
        snapshots: list[IndicatorSnapshot] = []

        for index, bar in enumerate(bars):
            if index > 0:
                ema12 = self._ema(closes[index], ema12, 12)
                ema26 = self._ema(closes[index], ema26, 26)
            dif = ema12 - ema26
            dea = dif if index == 0 else self._ema(dif, dea, 9)
            macd_hist = 2 * (dif - dea)

            ma5 = self._window_mean(closes, index, 5)
            ma10 = self._window_mean(closes, index, 10)
            ma20 = self._window_mean(closes, index, 20)
            ma60 = self._window_mean(closes, index, 60)

            rsi14: float | None = None
            if index == 14:
                changes = [closes[position] - closes[position - 1] for position in range(1, 15)]
                average_gain = sum(max(change, 0.0) for change in changes) / 14
                average_loss = sum(max(-change, 0.0) for change in changes) / 14
                rsi14 = self._rsi(average_gain, average_loss)
            elif index > 14:
                change = closes[index] - closes[index - 1]
                assert average_gain is not None and average_loss is not None
                average_gain = (average_gain * 13 + max(change, 0.0)) / 14
                average_loss = (average_loss * 13 + max(-change, 0.0)) / 14
                rsi14 = self._rsi(average_gain, average_loss)

            boll_mid: float | None = None
            boll_upper: float | None = None
            boll_lower: float | None = None
            if ma20 is not None:
                window = closes[index - 19 : index + 1]
                deviation = sqrt(sum((value - ma20) ** 2 for value in window) / 20)
                boll_mid = ma20
                boll_upper = ma20 + 2 * deviation
                boll_lower = ma20 - 2 * deviation

            volume_ratio = self._volume_ratio(volumes, index)
            unavailable = frozenset(
                name
                for name, value in (
                    ("ma5", ma5),
                    ("ma10", ma10),
                    ("ma20", ma20),
                    ("ma60", ma60),
                    ("rsi14", rsi14),
                    ("boll", boll_mid),
                    ("volume_ratio_5_20", volume_ratio),
                )
                if value is None
            )

            snapshots.append(
                IndicatorSnapshot(
                    trade_date=bar.trade_date,
                    ma5=ma5,
                    ma10=ma10,
                    ma20=ma20,
                    ma60=ma60,
                    dif=dif,
                    dea=dea,
                    macd_hist=macd_hist,
                    rsi14=rsi14,
                    boll_mid=boll_mid,
                    boll_upper=boll_upper,
                    boll_lower=boll_lower,
                    volume_ratio_5_20=volume_ratio,
                    unavailable=unavailable,
                )
            )

        return snapshots

    @staticmethod
    def _ema(value: float, previous: float, period: int) -> float:
        alpha = 2 / (period + 1)
        return alpha * value + (1 - alpha) * previous

    @staticmethod
    def _window_mean(values: list[float], index: int, window: int) -> float | None:
        if index + 1 < window:
            return None
        selected = values[index - window + 1 : index + 1]
        return sum(selected) / window

    @staticmethod
    def _rsi(average_gain: float, average_loss: float) -> float:
        if average_gain == 0 and average_loss == 0:
            return 50.0
        if average_loss == 0:
            return 100.0
        if average_gain == 0:
            return 0.0
        relative_strength = average_gain / average_loss
        return 100 - 100 / (1 + relative_strength)

    @staticmethod
    def _volume_ratio(volumes: list[int], index: int) -> float | None:
        if index + 1 < 20:
            return None
        average5 = sum(volumes[index - 4 : index + 1]) / 5
        average20 = sum(volumes[index - 19 : index + 1]) / 20
        if average20 == 0:
            return None
        return average5 / average20
