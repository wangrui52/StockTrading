from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    api_version: str


class BatchContext(BaseModel):
    trade_date: date
    batch_id: int
    rule_version: str


class SyncCreatedResponse(BaseModel):
    job_id: int
    batch_id: int


class SyncJobResponse(BaseModel):
    id: int
    target_trade_date: date | None
    status: str
    stage: str
    progress: float
    completed_count: int
    failed_count: int
    error_summary: str | None


class SystemStatusResponse(BaseModel):
    active_batch: BatchContext | None
    latest_sync: SyncJobResponse | None


class CandidateItem(BaseModel):
    market: str
    stock_code: str
    score: float
    reasons: list[str]


class MarketSummary(BaseModel):
    up: int
    down: int
    flat: int
    amount: float


class DashboardResponse(BatchContext):
    completeness_rate: float
    candidates: list[CandidateItem]
    market_summary: MarketSummary


class PriceItem(BaseModel):
    trade_date: date
    adjustment: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    pct_change: float | None
    turnover_rate: float | None


class StockDetailResponse(BatchContext):
    market: str
    stock_code: str
    stock_name: str
    price: PriceItem | None


class PriceSeriesResponse(BatchContext):
    items: list[PriceItem]


class IndicatorSeriesResponse(BatchContext):
    items: list[dict[str, Any]]


class SignalItem(BaseModel):
    id: int
    market: str
    stock_code: str
    trade_date: date
    rule_code: str
    payload: dict[str, Any]


class SignalSeriesResponse(BatchContext):
    items: list[SignalItem]


class ScreeningResponse(BatchContext):
    items: list[CandidateItem]


class WatchlistItemResponse(BaseModel):
    id: int
    group_id: int
    market: str
    stock_code: str
    note: str | None


class WatchlistResponse(BaseModel):
    items: list[WatchlistItemResponse]


class AlertItem(SignalItem):
    status: str


class AlertListResponse(BatchContext):
    items: list[AlertItem]


class AlertStateResponse(BaseModel):
    id: int
    status: str
    confirmed_at: datetime | None


class ReportResponse(BatchContext):
    id: int
    market: str
    stock_code: str
    template_version: str
    report_version: int
    content: str
