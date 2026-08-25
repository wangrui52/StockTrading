from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.dashboard import context
from app.infrastructure.models import CandidateResult, DataBatch


def screen(session: Session, batch: DataBatch, minimum_score: float) -> dict[str, Any]:
    rows = session.scalars(
        select(CandidateResult)
        .where(
            CandidateResult.batch_id == batch.id,
            CandidateResult.score >= minimum_score,
        )
        .order_by(CandidateResult.score.desc(), CandidateResult.stock_code)
    ).all()
    return {
        **context(batch),
        "items": [
            {
                "market": item.market,
                "stock_code": item.stock_code,
                "score": item.score,
                "reasons": item.reasons,
            }
            for item in rows
        ],
    }
