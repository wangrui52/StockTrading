from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class AIRecommendationItem(BaseModel):
    recommendation: Literal["FOCUS", "WATCH", "AVOID"]
    ai_score: int
    horizon_trading_days: Literal[1, 3, 5]
    reasons: list[str]
    risks: list[str]
    invalidation: str
    confidence: float
    provider: str
    model: str
    run_version: int


class CandidateItem(BaseModel):
    market: str
    stock_code: str
    stock_name: str | None = None
    score: float
    reasons: list[str]
    close: float | None = None
    pct_change: float | None = None
    rsi14: float | None = None
    volume_ratio: float | None = None
    outcome_status: Literal["PENDING", "PARTIAL", "COMPLETED", "UNAVAILABLE"] = (
        "PENDING"
    )
    ai_recommendation: AIRecommendationItem | None = None


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


class StockCandidateOutcomeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    horizon_trading_days: int
    status: Literal["PENDING", "COMPLETED", "UNAVAILABLE"]
    reference_trade_date: date | None
    evaluation_trade_date: date | None
    expected_evaluation_trade_date: date | None = Field(
        description="由完整权威交易日历确定的预计评价日；不代表评价已完成"
    )
    reference_price: float | None
    evaluation_price: float | None
    return_rate: float | None
    mfe: float | None
    mae: float | None
    unavailable_reason: str | None
    calculation_version: str


class StockDetailResponse(BatchContext):
    market: str
    stock_code: str
    stock_name: str
    industry: str | None
    price: PriceItem | None
    trend: str
    risk_level: str
    risk_reasons: list[str]
    candidate_outcomes: list[StockCandidateOutcomeItem]


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


class RealtimeQuoteResponse(BaseModel):
    market: str
    stock_code: str
    stock_name: str
    latest_price: float | None
    pct_change: float | None
    volume: int
    amount: float
    quoted_at: datetime


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
    realtime: RealtimeQuoteResponse | None = None
    ai_analysis: AIRecommendationItem | None = None


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


class StrategyOutcomeFilters(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rule_version: str | None
    latest_trading_days: int | None
    horizon: int | None
    date_from: date | None
    date_to: date | None
    status: str | None


class StrategyOutcomeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    candidate_result_id: int
    market: str
    stock_code: str
    stock_name: str | None
    source_batch_id: int
    evaluation_batch_id: int | None
    source_trade_date: date
    rule_version: str
    horizon_trading_days: int
    reference_trade_date: date | None
    evaluation_trade_date: date | None
    expected_evaluation_trade_date: date | None = Field(
        description="由完整权威交易日历确定的预计评价日；不代表评价已完成"
    )
    reference_price: float | None
    evaluation_price: float | None
    return_rate: float | None
    mfe: float | None
    mae: float | None
    status: str
    unavailable_reason: str | None
    calculation_version: str
    updated_at: datetime


class StrategyOutcomePage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[StrategyOutcomeView]
    total: int
    page: int
    page_size: int
    calculation_version: str
    filters: StrategyOutcomeFilters
    data_date: date | None


class StrategyOutcomeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total: int
    completed: int
    unavailable: int
    pending: int
    sample_size: int
    completion_rate: float
    mean_return_rate: float | None
    median_return_rate: float | None
    positive_return_ratio: float | None
    mean_mfe: float | None
    mean_mae: float | None
    max_drawdown_approx: float | None = Field(
        description=(
            "COMPLETED 样本持有窗口 MAE 的最差值（最小值，负百分数）；"
            "仅为样本级近似，不是资金曲线最大回撤"
        )
    )
    insufficient_sample: bool
    calculation_version: str
    filters: StrategyOutcomeFilters
    data_date: date | None


class CandidateOutcomes(BaseModel):
    items: list[StrategyOutcomeView]
    calculation_version: str


class OutcomeRunCreateRequest(BaseModel):
    evaluation_batch_id: int = Field(gt=0)


class OutcomeRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    evaluation_batch_id: int
    calculation_version: str
    status: str
    expected_count: int
    completed_count: int
    unavailable_count: int
    pending_count: int
    started_at: datetime
    finished_at: datetime | None
    error_summary: str | None
