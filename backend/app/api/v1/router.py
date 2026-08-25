from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.sqlalchemy_repositories import SQLAlchemySignalStore
from app.api.v1.schemas import (
    AlertListResponse,
    AlertStateResponse,
    DashboardResponse,
    HealthResponse,
    IndicatorSeriesResponse,
    PriceSeriesResponse,
    ReportResponse,
    ScreeningResponse,
    SignalSeriesResponse,
    StockDetailResponse,
    SyncCreatedResponse,
    SyncJobResponse,
    SystemStatusResponse,
    WatchlistItemResponse,
    WatchlistResponse,
)
from app.application.dashboard import (
    active_batch,
    context,
    dashboard_payload,
    stock_name,
)
from app.application.reports import create_stock_report
from app.application.screening import screen
from app.application.watchlist import add_item, list_items
from app.infrastructure.models import (
    AlertEventState,
    AnalysisReport,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    SignalEvent,
    StockBasic,
    SyncJob,
    WatchlistItem,
)

router = APIRouter(prefix="/api/v1")


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class ScreeningRequest(BaseModel):
    minimum_score: float = 0


class WatchlistRequest(BaseModel):
    group_id: int
    market: str
    stock_code: str


class ReportRequest(BaseModel):
    market: str
    stock_code: str


class SyncRequest(BaseModel):
    target_trade_date: date


@router.get("/health", response_model=HealthResponse)
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "stock-trading-backend",
        "api_version": "v1",
    }


def session(request: Request):
    with request.app.state.session_factory() as value:
        yield value


SessionDep = Annotated[Session, Depends(session)]


def require_batch(db: Session) -> DataBatch:
    batch = active_batch(db)
    if batch is None:
        raise APIError(409, "NO_ACTIVE_BATCH", "当前没有可用数据批次")
    return batch


@router.get("/system/status", response_model=SystemStatusResponse)
def system_status(db: SessionDep) -> dict[str, Any]:
    batch = active_batch(db)
    latest_job = db.scalar(select(SyncJob).order_by(SyncJob.id.desc()))
    return {
        "active_batch": context(batch) if batch else None,
        "latest_sync": _sync_payload(latest_job) if latest_job else None,
    }


@router.post("/sync-jobs", status_code=status.HTTP_201_CREATED, response_model=SyncCreatedResponse)
def create_sync_job(payload: SyncRequest, request: Request) -> dict[str, Any]:
    result = request.app.state.sync_runner(payload.target_trade_date)
    return {"job_id": result.job_id, "batch_id": result.batch_id}


@router.get("/sync-jobs/{job_id}", response_model=SyncJobResponse)
def get_sync_job(job_id: int, db: SessionDep) -> dict[str, Any]:
    job = db.get(SyncJob, job_id)
    if job is None:
        raise APIError(404, "SYNC_JOB_NOT_FOUND", "同步任务不存在")
    return _sync_payload(job)


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: SessionDep) -> dict[str, Any]:
    return dashboard_payload(db, require_batch(db))


@router.get("/stocks/{market}/{stock_code}", response_model=StockDetailResponse)
def stock_detail(market: str, stock_code: str, db: SessionDep) -> dict[str, Any]:
    batch = require_batch(db)
    name = stock_name(db, market, stock_code)
    if name is None:
        raise APIError(404, "STOCK_NOT_FOUND", "股票不存在")
    price = db.scalar(
        select(DailyPrice).where(
            DailyPrice.batch_id == batch.id,
            DailyPrice.market == market,
            DailyPrice.stock_code == stock_code,
            DailyPrice.trade_date == batch.trade_date,
            DailyPrice.adjustment == "raw",
        )
    )
    return {
        **context(batch),
        "market": market,
        "stock_code": stock_code,
        "stock_name": name,
        "price": _price_payload(price) if price else None,
    }


@router.get("/stocks/{market}/{stock_code}/prices", response_model=PriceSeriesResponse)
def stock_prices(market: str, stock_code: str, db: SessionDep) -> dict[str, Any]:
    batch = require_batch(db)
    rows = db.scalars(
        select(DailyPrice)
        .where(
            DailyPrice.batch_id == batch.id,
            DailyPrice.market == market,
            DailyPrice.stock_code == stock_code,
        )
        .order_by(DailyPrice.trade_date)
    ).all()
    return {**context(batch), "items": [_price_payload(item) for item in rows]}


@router.get("/stocks/{market}/{stock_code}/indicators", response_model=IndicatorSeriesResponse)
def stock_indicators(market: str, stock_code: str, db: SessionDep) -> dict[str, Any]:
    batch = require_batch(db)
    rows = db.scalars(
        select(DailyIndicator)
        .where(
            DailyIndicator.batch_id == batch.id,
            DailyIndicator.market == market,
            DailyIndicator.stock_code == stock_code,
        )
        .order_by(DailyIndicator.trade_date)
    ).all()
    return {
        **context(batch),
        "items": [{"trade_date": item.trade_date, **item.values} for item in rows],
    }


@router.get("/stocks/{market}/{stock_code}/signals", response_model=SignalSeriesResponse)
def stock_signals(market: str, stock_code: str, db: SessionDep) -> dict[str, Any]:
    batch = require_batch(db)
    rows = db.scalars(
        select(SignalEvent)
        .where(
            SignalEvent.batch_id == batch.id,
            SignalEvent.market == market,
            SignalEvent.stock_code == stock_code,
        )
        .order_by(SignalEvent.trade_date.desc())
    ).all()
    return {**context(batch), "items": [_signal_payload(item) for item in rows]}


@router.post("/screenings", response_model=ScreeningResponse)
def screenings(payload: ScreeningRequest, db: SessionDep) -> dict[str, Any]:
    return screen(db, require_batch(db), payload.minimum_score)


@router.get("/watchlist/items", response_model=WatchlistResponse)
def watchlist_items(db: SessionDep) -> dict[str, Any]:
    return {"items": [_watchlist_payload(item) for item in list_items(db)]}


@router.post(
    "/watchlist/items",
    status_code=status.HTTP_201_CREATED,
    response_model=WatchlistItemResponse,
)
def create_watchlist_item(payload: WatchlistRequest, db: SessionDep) -> dict[str, Any]:
    if db.get(StockBasic, _stock_id(db, payload.market, payload.stock_code)) is None:
        raise APIError(404, "STOCK_NOT_FOUND", "股票不存在")
    item = add_item(db, **payload.model_dump())
    db.commit()
    return _watchlist_payload(item)


@router.delete("/watchlist/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist_item(item_id: int, db: SessionDep) -> Response:
    item = db.get(WatchlistItem, item_id)
    if item is None:
        raise APIError(404, "WATCHLIST_ITEM_NOT_FOUND", "自选项不存在")
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/alerts", response_model=AlertListResponse)
def alerts(db: SessionDep) -> dict[str, Any]:
    batch = require_batch(db)
    signals = db.scalars(
        select(SignalEvent).where(SignalEvent.batch_id == batch.id).order_by(SignalEvent.id.desc())
    ).all()
    items = []
    for signal in signals:
        state_row = db.scalar(
            select(AlertEventState).where(AlertEventState.signal_event_id == signal.id)
        )
        if state_row is None:
            state_row = AlertEventState(signal_event_id=signal.id, status="TRIGGERED")
            db.add(state_row)
        items.append({**_signal_payload(signal), "status": state_row.status})
    db.commit()
    return {**context(batch), "items": items}


@router.post("/alerts/{signal_id}/confirm", response_model=AlertStateResponse)
def confirm_alert(signal_id: int, db: SessionDep) -> dict[str, Any]:
    if db.get(SignalEvent, signal_id) is None:
        raise APIError(404, "ALERT_NOT_FOUND", "提醒不存在")
    state_row = SQLAlchemySignalStore(db).confirm(signal_id, confirmed_at=datetime.now(UTC))
    db.commit()
    return {"id": signal_id, "status": state_row.status, "confirmed_at": state_row.confirmed_at}


@router.post("/reports", status_code=status.HTTP_201_CREATED, response_model=ReportResponse)
def create_report(payload: ReportRequest, db: SessionDep) -> dict[str, Any]:
    batch = require_batch(db)
    if stock_name(db, payload.market, payload.stock_code) is None:
        raise APIError(404, "STOCK_NOT_FOUND", "股票不存在")
    report = create_stock_report(db, batch, **payload.model_dump())
    db.commit()
    return _report_payload(report)


@router.get("/reports/{report_id}", response_model=ReportResponse)
def get_report(report_id: int, db: SessionDep) -> dict[str, Any]:
    report = db.get(AnalysisReport, report_id)
    if report is None:
        raise APIError(404, "REPORT_NOT_FOUND", "报告不存在")
    return _report_payload(report)


@router.get("/reports/{report_id}/export", response_class=PlainTextResponse)
def export_report(report_id: int, db: SessionDep) -> str:
    report = db.get(AnalysisReport, report_id)
    if report is None:
        raise APIError(404, "REPORT_NOT_FOUND", "报告不存在")
    return report.content


def _stock_id(db: Session, market: str, stock_code: str) -> int | None:
    return db.scalar(
        select(StockBasic.id).where(
            StockBasic.market == market, StockBasic.stock_code == stock_code
        )
    )


def _price_payload(item: DailyPrice) -> dict[str, Any]:
    return {
        "trade_date": item.trade_date,
        "adjustment": item.adjustment,
        "open": item.open,
        "high": item.high,
        "low": item.low,
        "close": item.close,
        "volume": item.volume,
        "amount": item.amount,
        "pct_change": item.pct_change,
        "turnover_rate": item.turnover_rate,
    }


def _signal_payload(item: SignalEvent) -> dict[str, Any]:
    return {
        "id": item.id,
        "market": item.market,
        "stock_code": item.stock_code,
        "trade_date": item.trade_date,
        "rule_code": item.rule_code,
        "payload": item.payload,
    }


def _watchlist_payload(item: WatchlistItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "group_id": item.group_id,
        "market": item.market,
        "stock_code": item.stock_code,
        "note": item.note,
    }


def _report_payload(item: AnalysisReport) -> dict[str, Any]:
    return {
        "id": item.id,
        "batch_id": item.batch_id,
        "market": item.market,
        "stock_code": item.stock_code,
        "trade_date": item.trade_date,
        "rule_version": item.rule_version,
        "template_version": item.template_version,
        "report_version": item.report_version,
        "content": item.content,
    }


def _sync_payload(job: SyncJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "target_trade_date": job.target_trade_date,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "completed_count": job.completed_count,
        "failed_count": job.failed_count,
        "error_summary": job.error_summary,
    }
