from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import CandidateResult, DailyPrice, DataBatch, IndexDaily, StockBasic


def active_batch(session: Session) -> DataBatch | None:
    return session.scalar(select(DataBatch).where(DataBatch.is_active.is_(True)))


def context(batch: DataBatch) -> dict[str, Any]:
    return {
        "trade_date": batch.trade_date,
        "batch_id": batch.id,
        "rule_version": batch.rule_version,
        "batch_status": batch.status,
        "risk_acknowledged": batch.risk_acknowledged,
    }


def dashboard_payload(session: Session, batch: DataBatch) -> dict[str, Any]:
    candidates = session.scalars(
        select(CandidateResult)
        .where(CandidateResult.batch_id == batch.id)
        .order_by(
            CandidateResult.positive_event_count.desc(),
            CandidateResult.volume_ratio.desc(),
            CandidateResult.pct_change.desc(),
            CandidateResult.stock_code,
        )
        .limit(20)
    ).all()
    indices = [
        item
        for code in ("000001", "399001", "399006", "899050")
        if (
            item := session.scalar(
                select(IndexDaily)
                .where(IndexDaily.index_code == code, IndexDaily.trade_date <= batch.trade_date)
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
                "score": item.score,
                "reasons": item.reasons,
            }
            for item in candidates
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


def _market_summary(session: Session, batch_id: int, trade_date: date) -> dict[str, Any]:
    rows = session.scalars(
        select(DailyPrice).where(
            DailyPrice.batch_id == batch_id,
            DailyPrice.trade_date == trade_date,
            DailyPrice.adjustment == "raw",
        )
    ).all()
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
