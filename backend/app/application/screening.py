from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.dashboard import context
from app.infrastructure.models import DailyIndicator, DailyPrice, DataBatch, SignalEvent, StockBasic


def screen(session: Session, batch: DataBatch, criteria: dict[str, Any]) -> dict[str, Any]:
    """只读取已缓存的价格、指标和信号，按 AND 组合条件稳定筛选。"""
    price_rows = session.scalars(
        select(DailyPrice).where(
            DailyPrice.batch_id == batch.id,
            DailyPrice.trade_date == batch.trade_date,
            DailyPrice.adjustment == "raw",
        )
    ).all()
    indicators = {
        (item.market, item.stock_code): item
        for item in session.scalars(
            select(DailyIndicator).where(
                DailyIndicator.batch_id == batch.id,
                DailyIndicator.trade_date == batch.trade_date,
                DailyIndicator.rule_version == batch.rule_version,
            )
        )
    }
    stocks = {(item.market, item.stock_code): item for item in session.scalars(select(StockBasic))}
    signals: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in session.scalars(
        select(SignalEvent).where(
            SignalEvent.trade_date == batch.trade_date,
            SignalEvent.rule_version == batch.rule_version,
        )
    ):
        signals[(item.market, item.stock_code)].add(item.rule_code)
    listed_days = {
        (market, code): count
        for market, code, count in session.execute(
            select(DailyPrice.market, DailyPrice.stock_code, func.count(DailyPrice.id))
            .where(DailyPrice.batch_id == batch.id, DailyPrice.adjustment == "qfq")
            .group_by(DailyPrice.market, DailyPrice.stock_code)
        )
    }

    results: list[dict[str, Any]] = []
    for price in price_rows:
        key = (price.market, price.stock_code)
        indicator = indicators.get(key)
        stock = stocks.get(key)
        if indicator is None or stock is None:
            continue
        values = indicator.values
        if not _matches(criteria, price, values, stock, signals[key], listed_days.get(key, 0)):
            continue
        reasons = _reasons(criteria, price, values, signals[key])
        score = float(len(reasons))
        if score < criteria["minimum_score"]:
            continue
        results.append(
            {
                "market": price.market,
                "stock_code": price.stock_code,
                "score": score,
                "reasons": reasons or ["符合当前组合条件"],
                "close": price.close,
                "pct_change": price.pct_change,
                "rsi14": values.get("rsi14"),
            }
        )

    results.sort(key=lambda item: (-item["score"], item["market"], item["stock_code"]))
    total = len(results)
    page = criteria["page"]
    page_size = criteria["page_size"]
    start = (page - 1) * page_size
    return {
        **context(batch),
        "items": results[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _matches(
    criteria: dict[str, Any],
    price: DailyPrice,
    values: dict[str, Any],
    stock: StockBasic,
    signal_codes: set[str],
    listed_days: int,
) -> bool:
    checks = [
        not criteria["markets"] or price.market in criteria["markets"],
        criteria["pct_change_min"] is None
        or (price.pct_change is not None and price.pct_change >= criteria["pct_change_min"]),
        criteria["pct_change_max"] is None
        or (price.pct_change is not None and price.pct_change <= criteria["pct_change_max"]),
        criteria["volume_ratio_min"] is None
        or _number(values, "volume_ratio_5_20") >= criteria["volume_ratio_min"],
        criteria["close_above_ma20"] is None
        or (price.close > _number(values, "ma20")) == criteria["close_above_ma20"],
        criteria["ma5_above_ma20"] is None
        or (_number(values, "ma5") > _number(values, "ma20")) == criteria["ma5_above_ma20"],
        criteria["rsi_min"] is None or _number(values, "rsi14") >= criteria["rsi_min"],
        criteria["rsi_max"] is None or _number(values, "rsi14") <= criteria["rsi_max"],
        criteria["include_st"] or not stock.is_st,
        criteria["include_suspended"] or not price.is_suspended,
        criteria["minimum_listed_days"] is None or listed_days >= criteria["minimum_listed_days"],
        not criteria["macd_filters"]
        or bool(set(criteria["macd_filters"]) & _macd_states(values, signal_codes)),
    ]
    return all(checks)


def _number(values: dict[str, Any], key: str) -> float:
    value = values.get(key)
    return float(value) if value is not None else float("-inf")


def _macd_states(values: dict[str, Any], signal_codes: set[str]) -> set[str]:
    states = set(signal_codes)
    dif = values.get("dif")
    dea = values.get("dea")
    if dif is not None and dea is not None:
        states.add("MACD_BULLISH" if dif > dea else "MACD_BEARISH")
    return states


def _reasons(
    criteria: dict[str, Any],
    price: DailyPrice,
    values: dict[str, Any],
    signal_codes: set[str],
) -> list[str]:
    reasons: list[str] = []
    if criteria["close_above_ma20"] is not None:
        reasons.append(
            "CLOSE_ABOVE_MA20" if price.close > _number(values, "ma20") else "CLOSE_BELOW_MA20"
        )
    if criteria["ma5_above_ma20"] is not None:
        reasons.append(
            "MA5_ABOVE_MA20"
            if _number(values, "ma5") > _number(values, "ma20")
            else "MA5_BELOW_MA20"
        )
    reasons.extend(sorted(set(criteria["macd_filters"]) & _macd_states(values, signal_codes)))
    if criteria["rsi_min"] is not None or criteria["rsi_max"] is not None:
        reasons.append("RSI_RANGE")
    if criteria["volume_ratio_min"] is not None:
        reasons.append("VOLUME_RATIO")
    return reasons
