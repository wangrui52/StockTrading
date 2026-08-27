from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.adapters.sqlalchemy_repositories import SQLAlchemyBatchStore, SQLAlchemySignalStore
from app.api.v1.schemas import (
    AlertListResponse,
    AlertStateResponse,
    BatchActivationResponse,
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
    WatchlistGroupResponse,
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
    OperationLog,
    SignalEvent,
    StockBasic,
    SyncJob,
    WatchlistGroup,
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
    markets: list[str] = Field(default_factory=list)
    pct_change_min: float | None = None
    pct_change_max: float | None = None
    volume_ratio_min: float | None = None
    close_above_ma20: bool | None = None
    ma5_above_ma20: bool | None = None
    macd_filters: list[str] = Field(default_factory=list)
    rsi_min: float | None = None
    rsi_max: float | None = None
    include_st: bool = False
    include_suspended: bool = False
    minimum_listed_days: int | None = Field(default=None, ge=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def require_condition(self) -> "ScreeningRequest":
        selected = any(
            (
                self.minimum_score > 0,
                bool(self.markets),
                self.pct_change_min is not None,
                self.pct_change_max is not None,
                self.volume_ratio_min is not None,
                self.close_above_ma20 is not None,
                self.ma5_above_ma20 is not None,
                bool(self.macd_filters),
                self.rsi_min is not None,
                self.rsi_max is not None,
                self.include_st,
                self.include_suspended,
                self.minimum_listed_days is not None,
            )
        )
        if not selected:
            raise ValueError("至少选择一个筛选条件")
        return self


class WatchlistRequest(BaseModel):
    group_id: int
    market: str
    stock_code: str


class ReportRequest(BaseModel):
    market: str
    stock_code: str


class SyncRequest(BaseModel):
    target_trade_date: date | None = None


class BatchActivationRequest(BaseModel):
    force: bool = False


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
def create_sync_job(
    payload: SyncRequest, request: Request, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    target = payload.target_trade_date or request.app.state.latest_trade_date()
    if request.app.state.sync_prepare is not None:
        result, should_execute = request.app.state.sync_prepare(target)
        if should_execute:
            background_tasks.add_task(
                request.app.state.sync_execute,
                result.job_id,
                result.batch_id,
                target,
            )
    else:
        result = request.app.state.sync_runner(target)
    return {"job_id": result.job_id, "batch_id": result.batch_id}


@router.post(
    "/sync-jobs/{job_id}/retry",
    status_code=status.HTTP_201_CREATED,
    response_model=SyncCreatedResponse,
)
def retry_sync_job(
    job_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: SessionDep,
) -> dict[str, Any]:
    job = db.get(SyncJob, job_id)
    if job is None:
        raise APIError(404, "SYNC_JOB_NOT_FOUND", "同步任务不存在")
    if job.status != "FAILED" or job.target_trade_date is None:
        raise APIError(409, "SYNC_JOB_NOT_RETRYABLE", "仅失败的同步任务可重试")
    if request.app.state.sync_prepare is not None:
        result, should_execute = request.app.state.sync_prepare(job.target_trade_date)
        if should_execute:
            background_tasks.add_task(
                request.app.state.sync_execute,
                result.job_id,
                result.batch_id,
                job.target_trade_date,
            )
    else:
        result = request.app.state.sync_runner(job.target_trade_date)
    return {"job_id": result.job_id, "batch_id": result.batch_id}


@router.get("/sync-jobs/{job_id}", response_model=SyncJobResponse)
def get_sync_job(job_id: int, db: SessionDep) -> dict[str, Any]:
    job = db.get(SyncJob, job_id)
    if job is None:
        raise APIError(404, "SYNC_JOB_NOT_FOUND", "同步任务不存在")
    return _sync_payload(job)


@router.post("/data-batches/{batch_id}/activate", response_model=BatchActivationResponse)
def activate_data_batch(
    batch_id: int, payload: BatchActivationRequest, db: SessionDep
) -> dict[str, Any]:
    batch = db.get(DataBatch, batch_id)
    if batch is None:
        raise APIError(404, "DATA_BATCH_NOT_FOUND", "数据批次不存在")
    if batch.status == "FAILED":
        job = db.scalar(select(SyncJob).where(SyncJob.batch_id == batch_id))
        is_completeness_failure = bool(job and "数据完整率" in (job.error_summary or ""))
        if not payload.force or not is_completeness_failure:
            raise APIError(
                409, "BATCH_FORCE_CONFIRMATION_REQUIRED", "仅数据不完整批次可确认风险后强制切换"
            )
        batch.status = "READY_WITH_GAPS"
        batch.risk_acknowledged = True
    SQLAlchemyBatchStore(db).activate_ready_batch(batch.id)
    db.commit()
    return {**context(batch), "completeness_rate": batch.completeness_rate}


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: SessionDep) -> dict[str, Any]:
    return dashboard_payload(db, require_batch(db))


@router.get("/stocks/{market}/{stock_code}", response_model=StockDetailResponse)
def stock_detail(
    market: str,
    stock_code: str,
    db: SessionDep,
    source: str | None = Query(default=None, pattern="^(dashboard|screener|watchlist|direct)$"),
) -> dict[str, Any]:
    batch = require_batch(db)
    stock = db.scalar(
        select(StockBasic).where(StockBasic.market == market, StockBasic.stock_code == stock_code)
    )
    if stock is None:
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
    indicator = db.scalar(
        select(DailyIndicator).where(
            DailyIndicator.batch_id == batch.id,
            DailyIndicator.market == market,
            DailyIndicator.stock_code == stock_code,
            DailyIndicator.trade_date == batch.trade_date,
        )
    )
    signals = list(
        db.scalars(
            select(SignalEvent).where(
                SignalEvent.batch_id == batch.id,
                SignalEvent.market == market,
                SignalEvent.stock_code == stock_code,
                SignalEvent.trade_date == batch.trade_date,
                SignalEvent.rule_version == batch.rule_version,
            )
        )
    )
    signal_codes = {item.rule_code for item in signals}
    high_risk = sorted(signal_codes & {"FALL_BELOW_MA20", "MACD_DEATH_CROSS", "DAILY_DROP"})
    values = indicator.values if indicator else {}
    rsi14 = values.get("rsi14")
    medium_risk = ["RSI_OVERHEATED"] if rsi14 is not None and rsi14 > 80 else []
    risk_reasons = high_risk or medium_risk
    risk_level = (
        "high" if high_risk else "medium" if medium_risk or batch.completeness_rate < 1 else "low"
    )
    trend = "停牌，未生成当日趋势" if price and price.is_suspended else "震荡"
    if (
        not (price and price.is_suspended)
        and values.get("ma5") is not None
        and values.get("ma20") is not None
    ):
        trend = "偏强" if values["ma5"] > values["ma20"] else "偏弱"
    payload = {
        **context(batch),
        "market": market,
        "stock_code": stock_code,
        "stock_name": stock.stock_name,
        "industry": stock.industry,
        "price": _price_payload(price) if price else None,
        "trend": trend,
        "risk_level": risk_level,
        "risk_reasons": risk_reasons,
    }
    db.add(
        OperationLog(
            event_name="stock_detail_view",
            page="stock-detail",
            batch_id=batch.id,
            market=market,
            stock_code=stock_code,
            details={"source": source or "direct"},
        )
    )
    db.commit()
    return payload


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
            SignalEvent.trade_date <= batch.trade_date,
            SignalEvent.rule_version == batch.rule_version,
        )
        .order_by(SignalEvent.trade_date.desc())
        .limit(10)
    ).all()
    return {**context(batch), "items": [_signal_payload(item) for item in rows]}


@router.post("/screenings", response_model=ScreeningResponse)
def screenings(payload: ScreeningRequest, db: SessionDep) -> dict[str, Any]:
    batch = require_batch(db)
    result = screen(db, batch, payload.model_dump())
    db.add(
        OperationLog(
            event_name="screener_search",
            page="screener",
            batch_id=batch.id,
            details={"result_count": result["total"]},
        )
    )
    db.commit()
    return result


@router.get("/watchlist/items", response_model=WatchlistResponse)
def watchlist_items(db: SessionDep) -> dict[str, Any]:
    batch = active_batch(db)
    return {"items": [_watchlist_payload(db, item, batch) for item in list_items(db)]}


@router.get("/watchlist/groups", response_model=WatchlistGroupResponse)
def watchlist_groups(db: SessionDep) -> dict[str, Any]:
    rows = db.scalars(select(WatchlistGroup).order_by(WatchlistGroup.sort_order, WatchlistGroup.id))
    return {
        "items": [
            {"id": item.id, "name": item.name, "sort_order": item.sort_order} for item in rows
        ]
    }


@router.post(
    "/watchlist/items",
    status_code=status.HTTP_201_CREATED,
    response_model=WatchlistItemResponse,
)
def create_watchlist_item(payload: WatchlistRequest, db: SessionDep) -> dict[str, Any]:
    if db.get(StockBasic, _stock_id(db, payload.market, payload.stock_code)) is None:
        raise APIError(404, "STOCK_NOT_FOUND", "股票不存在")
    if db.get(WatchlistGroup, payload.group_id) is None:
        raise APIError(404, "WATCHLIST_GROUP_NOT_FOUND", "自选分组不存在")
    item = add_item(db, **payload.model_dump())
    db.commit()
    return _watchlist_payload(db, item, active_batch(db))


@router.delete("/watchlist/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist_item(item_id: int, db: SessionDep) -> Response:
    item = db.get(WatchlistItem, item_id)
    if item is None:
        raise APIError(404, "WATCHLIST_ITEM_NOT_FOUND", "自选项不存在")
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/alerts", response_model=AlertListResponse)
def alerts(
    db: SessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    watchlist_only: bool = False,
) -> dict[str, Any]:
    batch = require_batch(db)
    query = select(SignalEvent).where(
        SignalEvent.batch_id == batch.id,
        SignalEvent.trade_date == batch.trade_date,
        SignalEvent.rule_version == batch.rule_version,
    )
    if watchlist_only:
        watched = select(WatchlistItem.market, WatchlistItem.stock_code)
        query = query.where(tuple_(SignalEvent.market, SignalEvent.stock_code).in_(watched))
    signals = db.scalars(query.order_by(SignalEvent.id.desc()).limit(limit)).all()
    items = []
    for signal in signals:
        state_row = db.scalar(
            select(AlertEventState).where(AlertEventState.signal_event_id == signal.id)
        )
        items.append(
            {
                **_signal_payload(signal),
                "status": state_row.status if state_row else "TRIGGERED",
            }
        )
    return {**context(batch), "items": items}


@router.post("/alerts/{signal_id}/confirm", response_model=AlertStateResponse)
def confirm_alert(signal_id: int, db: SessionDep) -> dict[str, Any]:
    batch = require_batch(db)
    signal = db.get(SignalEvent, signal_id)
    if signal is None or signal.batch_id != batch.id:
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
    return _report_payload(db, report)


@router.get("/reports/{report_id}", response_model=ReportResponse)
def get_report(report_id: int, db: SessionDep) -> dict[str, Any]:
    report = db.get(AnalysisReport, report_id)
    if report is None:
        raise APIError(404, "REPORT_NOT_FOUND", "报告不存在")
    return _report_payload(db, report)


@router.get("/reports/{report_id}/export", response_class=PlainTextResponse)
def export_report(report_id: int, db: SessionDep) -> PlainTextResponse:
    report = db.get(AnalysisReport, report_id)
    if report is None:
        raise APIError(404, "REPORT_NOT_FOUND", "报告不存在")
    filename = f"{report.trade_date.isoformat()}-{report.stock_code}-{report.report_version}.md"
    return PlainTextResponse(
        report.content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
        "is_suspended": item.is_suspended,
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


def _watchlist_payload(db: Session, item: WatchlistItem, batch: DataBatch | None) -> dict[str, Any]:
    group = db.get(WatchlistGroup, item.group_id)
    stock = db.scalar(
        select(StockBasic).where(
            StockBasic.market == item.market,
            StockBasic.stock_code == item.stock_code,
        )
    )
    price = None
    signals: list[SignalEvent] = []
    if batch is not None:
        price = db.scalar(
            select(DailyPrice).where(
                DailyPrice.batch_id == batch.id,
                DailyPrice.market == item.market,
                DailyPrice.stock_code == item.stock_code,
                DailyPrice.trade_date == batch.trade_date,
                DailyPrice.adjustment == "raw",
            )
        )
        signals = list(
            db.scalars(
                select(SignalEvent).where(
                    SignalEvent.batch_id == batch.id,
                    SignalEvent.market == item.market,
                    SignalEvent.stock_code == item.stock_code,
                    SignalEvent.trade_date == batch.trade_date,
                    SignalEvent.rule_version == batch.rule_version,
                )
            )
        )
    states = [
        db.scalar(select(AlertEventState).where(AlertEventState.signal_event_id == signal.id))
        for signal in signals
    ]
    alert_status = "UNTRIGGERED"
    if signals:
        alert_status = (
            "CONFIRMED"
            if all(state and state.status == "CONFIRMED" for state in states)
            else "TRIGGERED"
        )
    risk_order = {"low": 1, "medium": 2, "high": 3}
    risk_level = max(
        (str(signal.payload.get("risk_level", "low")) for signal in signals),
        key=lambda value: risk_order.get(value, 0),
        default=None,
    )
    return {
        "id": item.id,
        "group_id": item.group_id,
        "group_name": group.name if group else None,
        "market": item.market,
        "stock_code": item.stock_code,
        "stock_name": stock.stock_name if stock else None,
        "note": item.note,
        "trade_date": batch.trade_date if batch else None,
        "close": price.close if price else None,
        "pct_change": price.pct_change if price else None,
        "signal_codes": sorted(signal.rule_code for signal in signals),
        "risk_level": risk_level,
        "alert_status": alert_status,
    }


def _report_payload(db: Session, item: AnalysisReport) -> dict[str, Any]:
    batch = db.get(DataBatch, item.batch_id)
    assert batch is not None
    return {
        "id": item.id,
        "batch_id": item.batch_id,
        "market": item.market,
        "stock_code": item.stock_code,
        "trade_date": item.trade_date,
        "rule_version": item.rule_version,
        "batch_status": batch.status,
        "risk_acknowledged": batch.risk_acknowledged,
        "template_version": item.template_version,
        "report_version": item.report_version,
        "content": item.content,
    }


def _sync_payload(job: SyncJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "batch_id": job.batch_id,
        "target_trade_date": job.target_trade_date,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "completed_count": job.completed_count,
        "failed_count": job.failed_count,
        "failed_items": job.failed_items,
        "error_summary": job.error_summary,
    }
