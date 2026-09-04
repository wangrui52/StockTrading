from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.application.batch_snapshot import price_rows
from app.infrastructure.models import (
    AIRecommendation,
    AIRecommendationRun,
    CandidateResult,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    IndexDaily,
    MarketBreadthSnapshot,
    StockBasic,
)


def active_batch(session: Session) -> DataBatch | None:
    return session.scalar(select(DataBatch).where(DataBatch.is_active.is_(True)))


def context(batch: DataBatch) -> dict[str, Any]:
    return {
        "source": batch.source,
        "trade_date": batch.trade_date,
        "batch_id": batch.id,
        "rule_version": batch.rule_version,
        "batch_status": batch.status,
        "risk_acknowledged": batch.risk_acknowledged,
    }


def dashboard_payload(
    session: Session,
    batch: DataBatch,
    outcome_statuses: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    candidates = session.execute(
        select(CandidateResult, StockBasic.stock_name)
        .outerjoin(
            StockBasic,
            (StockBasic.market == CandidateResult.market)
            & (StockBasic.stock_code == CandidateResult.stock_code),
        )
        .where(CandidateResult.batch_id == batch.id)
        .order_by(
            CandidateResult.positive_event_count.desc(),
            CandidateResult.volume_ratio.desc(),
            CandidateResult.pct_change.desc(),
            CandidateResult.stock_code,
        )
        .limit(20)
    ).all()
    candidate_symbols = [(item.market, item.stock_code) for item, _name in candidates]
    current_prices = (
        {
            (item.market, item.stock_code): item
            for item in session.scalars(
                select(DailyPrice).where(
                    DailyPrice.batch_id == batch.id,
                    DailyPrice.trade_date == batch.trade_date,
                    DailyPrice.adjustment == "raw",
                    tuple_(DailyPrice.market, DailyPrice.stock_code).in_(candidate_symbols),
                )
            )
        }
        if candidate_symbols
        else {}
    )
    current_indicators = (
        {
            (item.market, item.stock_code): item
            for item in session.scalars(
                select(DailyIndicator).where(
                    DailyIndicator.batch_id == batch.id,
                    DailyIndicator.trade_date == batch.trade_date,
                    tuple_(DailyIndicator.market, DailyIndicator.stock_code).in_(candidate_symbols),
                )
            )
        }
        if candidate_symbols
        else {}
    )
    latest_ai_run = session.scalar(
        select(AIRecommendationRun)
        .where(
            AIRecommendationRun.batch_id == batch.id,
            AIRecommendationRun.scope == "candidate",
        )
        .order_by(AIRecommendationRun.version.desc())
    )
    ai_recommendations = (
        {
            (item.market, item.stock_code): item
            for item in session.scalars(
                select(AIRecommendation).where(AIRecommendation.run_id == latest_ai_run.id)
            )
        }
        if latest_ai_run is not None
        else {}
    )
    indices = [
        item
        for code in ("000001", "399001", "399006", "899050")
        if (
            item := session.scalar(
                select(IndexDaily)
                .where(IndexDaily.index_code == code, IndexDaily.batch_id == batch.id)
                .order_by(IndexDaily.trade_date.desc(), IndexDaily.id.desc())
            )
        )
        is not None
    ]
    return {
        **context(batch),
        "completeness_rate": batch.completeness_rate,
        "candidates": [
            {
                "market": item.market,
                "stock_code": item.stock_code,
                "stock_name": name,
                "score": item.score,
                "reasons": item.reasons,
                "close": (
                    price.close
                    if (price := current_prices.get((item.market, item.stock_code))) is not None
                    else None
                ),
                "pct_change": item.pct_change,
                "rsi14": (
                    indicator.values.get("rsi14")
                    if (
                        indicator := current_indicators.get((item.market, item.stock_code))
                    )
                    is not None
                    else None
                ),
                "volume_ratio": item.volume_ratio,
                "outcome_status": (outcome_statuses or {}).get(item.id, "PENDING"),
                "ai_recommendation": (
                    {
                        "recommendation": recommendation.recommendation,
                        "ai_score": recommendation.ai_score,
                        "horizon_trading_days": recommendation.horizon_trading_days,
                        "reasons": recommendation.reasons,
                        "risks": recommendation.risks,
                        "invalidation": recommendation.invalidation,
                        "confidence": recommendation.confidence,
                        "provider": latest_ai_run.provider,
                        "model": latest_ai_run.model,
                        "run_version": latest_ai_run.version,
                    }
                    if (recommendation := ai_recommendations.get((item.market, item.stock_code)))
                    is not None
                    else None
                ),
            }
            for item, name in candidates
        ],
        "market_summary": (
            _market_summary(session, batch.id, batch.trade_date)
            if batch.completeness_rate >= 0.99
            else None
        ),
        "indices": [
            {
                "index_code": item.index_code,
                "trade_date": item.trade_date,
                "close": item.close,
                "pct_change": item.pct_change,
            }
            for item in indices
        ],
    }


def _market_summary(
    session: Session, batch_id: int, trade_date: date
) -> dict[str, Any] | None:
    batch = session.get(DataBatch, batch_id)
    snapshot = session.scalar(
        select(MarketBreadthSnapshot).where(
            MarketBreadthSnapshot.source == batch.source,
            MarketBreadthSnapshot.trade_date == trade_date,
            MarketBreadthSnapshot.scope == "ALL",
        )
    )
    if snapshot is not None:
        if not snapshot.is_complete:
            return None
        return {
            "up": snapshot.up_count,
            "down": snapshot.down_count,
            "flat": snapshot.flat_count,
            "amount": snapshot.amount,
        }
    rows = price_rows(
        session, batch_id, trade_date=trade_date, adjustment="raw"
    )
    if not rows or sum(item.pct_change is not None for item in rows) / len(rows) < 0.99:
        return None
    return {
        "up": sum(1 for item in rows if item.pct_change is not None and item.pct_change > 0),
        "down": sum(1 for item in rows if item.pct_change is not None and item.pct_change < 0),
        "flat": sum(1 for item in rows if item.pct_change == 0),
        "amount": sum(item.amount for item in rows),
    }


def stock_name(session: Session, market: str, stock_code: str) -> str | None:
    return session.scalar(
        select(StockBasic.stock_name).where(
            StockBasic.market == market, StockBasic.stock_code == stock_code
        )
    )
