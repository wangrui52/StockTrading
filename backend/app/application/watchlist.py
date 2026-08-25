from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import WatchlistItem


def list_items(session: Session) -> list[WatchlistItem]:
    return list(session.scalars(select(WatchlistItem).order_by(WatchlistItem.id)).all())


def add_item(session: Session, *, group_id: int, market: str, stock_code: str) -> WatchlistItem:
    existing = session.scalar(
        select(WatchlistItem).where(
            WatchlistItem.market == market,
            WatchlistItem.stock_code == stock_code,
        )
    )
    if existing is not None:
        existing.group_id = group_id
        return existing
    item = WatchlistItem(group_id=group_id, market=market, stock_code=stock_code)
    session.add(item)
    session.flush()
    return item
