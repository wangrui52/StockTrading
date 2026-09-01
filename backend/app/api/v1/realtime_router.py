from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import BaseModel

from app.api.v1.router import APIError
from app.api.v1.schemas import MarketSummary, RealtimeQuoteResponse
from app.application.realtime import EmptyWatchlistError, RealtimeScope

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])


class RealtimeJobResponse(BaseModel):
    id: int
    scope: RealtimeScope = "market"
    status: Literal["FETCHING", "READY", "PARTIAL", "FAILED"]
    stage: str
    total_count: int
    completed_count: int
    failed_count: int
    error_summary: str | None
    started_at: datetime
    finished_at: datetime | None


class RealtimeSnapshotResponse(BaseModel):
    refresh_id: int
    scope: RealtimeScope = "market"
    source: str
    started_at: datetime
    finished_at: datetime
    quote_date: date
    total_count: int
    received_count: int
    missing_count: int
    missing_symbols: list[str]
    stale_count: int
    unavailable_count: int
    market_summary: MarketSummary


class RealtimeStatusResponse(BaseModel):
    job: RealtimeJobResponse | None
    snapshot: RealtimeSnapshotResponse | None
    cooldown_until: datetime | None


class RealtimeQuotesResponse(BaseModel):
    snapshot: RealtimeSnapshotResponse | None
    items: list[RealtimeQuoteResponse]
    total: int
    page: int
    page_size: int


@router.post("/refresh", status_code=202, response_model=RealtimeJobResponse)
def refresh(request: Request, background_tasks: BackgroundTasks, scope: RealtimeScope = "market"):
    service = request.app.state.realtime
    try:
        job, execute = service.prepare(scope=scope)
    except EmptyWatchlistError as error:
        raise APIError(409, "EMPTY_WATCHLIST", str(error)) from error
    if execute:
        background_tasks.add_task(service.execute, job.id)
    return service.job_payload(job)


@router.get("/status", response_model=RealtimeStatusResponse)
def status(request: Request, scope: RealtimeScope = "market"):
    return request.app.state.realtime.status(scope=scope)


@router.get("/quotes", response_model=RealtimeQuotesResponse)
def quotes(
    request: Request,
    q: str = Query(default="", max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    scope: RealtimeScope = "market",
):
    return request.app.state.realtime.quotes(q=q, page=page, page_size=page_size, scope=scope)
