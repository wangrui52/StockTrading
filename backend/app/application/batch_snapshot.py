from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import DailyIndicator, DailyPrice, DataBatch, SignalEvent


def batch_lineage_ids(session: Session, batch_id: int) -> list[int]:
    """Return the immutable batch lineage from oldest ancestor to the requested batch."""
    lineage: list[int] = []
    seen: set[int] = set()
    current_id: int | None = batch_id
    while current_id is not None:
        if current_id in seen:
            raise ValueError(f"data batch lineage contains a cycle at batch {current_id}")
        seen.add(current_id)
        batch = session.get(DataBatch, current_id)
        if batch is None:
            raise ValueError(f"data batch {current_id} does not exist")
        lineage.append(current_id)
        current_id = batch.parent_batch_id
    lineage.reverse()
    return lineage


def price_rows(
    session: Session,
    batch_id: int,
    *,
    market: str | None = None,
    stock_code: str | None = None,
    trade_date=None,
    adjustment: str | None = None,
) -> list[DailyPrice]:
    lineage = batch_lineage_ids(session, batch_id)
    query = select(DailyPrice).where(DailyPrice.batch_id.in_(lineage))
    if market is not None:
        query = query.where(DailyPrice.market == market)
    if stock_code is not None:
        query = query.where(DailyPrice.stock_code == stock_code)
    if trade_date is not None:
        query = query.where(DailyPrice.trade_date == trade_date)
    if adjustment is not None:
        query = query.where(DailyPrice.adjustment == adjustment)
    rank = {value: index for index, value in enumerate(lineage)}
    rows = sorted(session.scalars(query), key=lambda item: rank[item.batch_id])
    latest = {
        (item.market, item.stock_code, item.trade_date, item.adjustment): item for item in rows
    }
    return sorted(
        latest.values(),
        key=lambda item: (item.market, item.stock_code, item.trade_date, item.adjustment),
    )


def indicator_rows(
    session: Session,
    batch_id: int,
    *,
    market: str | None = None,
    stock_code: str | None = None,
    trade_date=None,
    rule_version: str | None = None,
) -> list[DailyIndicator]:
    lineage = batch_lineage_ids(session, batch_id)
    query = select(DailyIndicator).where(DailyIndicator.batch_id.in_(lineage))
    if market is not None:
        query = query.where(DailyIndicator.market == market)
    if stock_code is not None:
        query = query.where(DailyIndicator.stock_code == stock_code)
    if trade_date is not None:
        query = query.where(DailyIndicator.trade_date == trade_date)
    if rule_version is not None:
        query = query.where(DailyIndicator.rule_version == rule_version)
    rank = {value: index for index, value in enumerate(lineage)}
    rows = sorted(session.scalars(query), key=lambda item: rank[item.batch_id])
    latest = {
        (item.market, item.stock_code, item.trade_date, item.rule_version): item for item in rows
    }
    return sorted(
        latest.values(), key=lambda item: (item.market, item.stock_code, item.trade_date)
    )
def signal_rows(
    session: Session,
    batch_id: int,
    *,
    market: str | None = None,
    stock_code: str | None = None,
    rule_version: str | None = None,
) -> list[SignalEvent]:
    lineage = batch_lineage_ids(session, batch_id)
    query = select(SignalEvent).where(SignalEvent.batch_id.in_(lineage))
    if market is not None:
        query = query.where(SignalEvent.market == market)
    if stock_code is not None:
        query = query.where(SignalEvent.stock_code == stock_code)
    if rule_version is not None:
        query = query.where(SignalEvent.rule_version == rule_version)
    rank = {value: index for index, value in enumerate(lineage)}
    rows = sorted(session.scalars(query), key=lambda item: rank[item.batch_id])
    latest = {
        (
            item.market,
            item.stock_code,
            item.trade_date,
            item.rule_code,
            item.rule_version,
        ): item
        for item in rows
    }
    return sorted(latest.values(), key=lambda item: (item.trade_date, item.id), reverse=True)
