from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    api_version: str


class BatchContext(BaseModel):
    source: str = "unknown"
    trade_date: date
    batch_id: int
    rule_version: str
    batch_status: str
    risk_acknowledged: bool


class SyncCreatedResponse(BaseModel):
    job_id: int
    batch_id: int


class BatchActivationResponse(BatchContext):
    completeness_rate: float


class SyncJobResponse(BaseModel):
    id: int
    batch_id: int | None
    target_trade_date: date | None
    status: str
    stage: str
    progress: float
    completed_count: int
    failed_count: int
    failed_items: list[str]
    error_summary: str | None


class SystemStatusResponse(BaseModel):
    active_batch: BatchContext | None
    latest_sync: SyncJobResponse | None


class CandidateItem(BaseModel):
    market: str
    stock_code: str
    stock_name: str | None = None
    score: float
    reasons: list[str]
    close: float | None = None
    pct_change: float | None = None
    rsi14: float | None = None


class MarketSummary(BaseModel):
    up: int
    down: int
    flat: int
    amount: float


class IndexItem(BaseModel):
    index_code: str
    trade_date: date
    close: float
    pct_change: float | None


class DashboardResponse(BatchContext):
    completeness_rate: float
    candidates: list[CandidateItem]
    market_summary: MarketSummary | None
    indices: list[IndexItem]


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
    is_suspended: bool


class StockDetailResponse(BatchContext):
    market: str
    stock_code: str
    stock_name: str
    industry: str | None
    price: PriceItem | None
    trend: str
    risk_level: str
    risk_reasons: list[str]


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
    total: int
    page: int
    page_size: int


class WatchlistItemResponse(BaseModel):
    id: int
    group_id: int
    group_name: str | None = None
    market: str
    stock_code: str
    stock_name: str | None = None
    note: str | None
    trade_date: date | None = None
    close: float | None = None
    pct_change: float | None = None
    signal_codes: list[str] = []
    risk_level: str | None = None
    alert_status: str = "UNTRIGGERED"


class WatchlistResponse(BaseModel):
    items: list[WatchlistItemResponse]


class WatchlistGroupItem(BaseModel):
    id: int
    name: str
    sort_order: int


class WatchlistGroupResponse(BaseModel):
    items: list[WatchlistGroupItem]


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
