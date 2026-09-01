from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RealtimeRefresh(Base):
    __tablename__ = "realtime_refresh"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), default="market", index=True)
    requested_symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="FETCHING")
    stage: Mapped[str] = mapped_column(String(16), default="STOCKS")
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RealtimeSnapshot(Base):
    __tablename__ = "realtime_snapshot"

    # id=1 全市场、id=2 自选股；分别原子替换，不向日线批次写入盘中数据。
    id: Mapped[int] = mapped_column(primary_key=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    quotes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)


class StockBasic(Base):
    __tablename__ = "stock_basic"
    __table_args__ = (UniqueConstraint("market", "stock_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(8))
    stock_code: Mapped[str] = mapped_column(String(16))
    stock_name: Mapped[str] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(64))
    list_date: Mapped[date | None] = mapped_column(Date)
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TradeCalendar(Base):
    __tablename__ = "trade_calendar"
    __table_args__ = (UniqueConstraint("market", "trade_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(8))
    trade_date: Mapped[date] = mapped_column(Date)
    is_open: Mapped[bool] = mapped_column(Boolean)


class SyncJob(Base):
    __tablename__ = "sync_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("data_batch.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(32))
    target_trade_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    stage: Mapped[str] = mapped_column(String(32), default="PENDING")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[list[str]] = mapped_column(JSON, default=list)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)


class DataBatch(Base):
    __tablename__ = "data_batch"
    __table_args__ = (
        Index("uq_data_batch_active", "is_active", unique=True, sqlite_where=text("is_active = 1")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64), default="unknown")
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default="BUILDING")
    completeness_rate: Mapped[float] = mapped_column(Float, default=0.0)
    rule_version: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    risk_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyPrice(Base):
    __tablename__ = "daily_price"
    __table_args__ = (
        UniqueConstraint("batch_id", "market", "stock_code", "trade_date", "adjustment"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("data_batch.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    adjustment: Mapped[str] = mapped_column(String(8))
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class IndexDaily(Base):
    __tablename__ = "index_daily"
    __table_args__ = (UniqueConstraint("batch_id", "index_code", "trade_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("data_batch.id"), index=True)
    index_code: Mapped[str] = mapped_column(String(16))
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DailyIndicator(Base):
    __tablename__ = "daily_indicator"
    __table_args__ = (
        UniqueConstraint("batch_id", "market", "stock_code", "trade_date", "rule_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("data_batch.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))
    stock_code: Mapped[str] = mapped_column(String(16))
    trade_date: Mapped[date] = mapped_column(Date)
    rule_version: Mapped[str] = mapped_column(String(32))
    values: Mapped[dict[str, Any]] = mapped_column(JSON)


class SignalEvent(Base):
    __tablename__ = "signal_event"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "market",
            "stock_code",
            "trade_date",
            "rule_code",
            "rule_version",
            name="uq_signal_batch_event",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("data_batch.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    rule_code: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AlertEventState(Base):
    __tablename__ = "alert_event_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_event_id: Mapped[int] = mapped_column(
        ForeignKey("signal_event.id"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="TRIGGERED")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CandidateResult(Base):
    __tablename__ = "candidate_result"
    __table_args__ = (UniqueConstraint("batch_id", "market", "stock_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("data_batch.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))
    stock_code: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list[str]] = mapped_column(JSON)
    positive_event_count: Mapped[int] = mapped_column(Integer, default=0)
    volume_ratio: Mapped[float | None] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)


class CandidateOutcome(Base):
    __tablename__ = "candidate_outcome"
    __table_args__ = (
        UniqueConstraint(
            "candidate_result_id",
            "horizon_trading_days",
            "calculation_version",
            "outcome_run_id",
            name="uq_candidate_outcome_snapshot",
        ),
        Index(
            "ix_candidate_outcome_rule_horizon_date_status",
            "rule_version",
            "horizon_trading_days",
            "source_trade_date",
            "status",
        ),
        Index(
            "ix_candidate_outcome_window",
            "calculation_version",
            "rule_version",
            "source_trade_date",
        ),
        Index(
            "ix_candidate_outcome_snapshot",
            "outcome_run_id",
            "calculation_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_result_id: Mapped[int] = mapped_column(ForeignKey("candidate_result.id"))
    source_batch_id: Mapped[int] = mapped_column(ForeignKey("data_batch.id"))
    evaluation_batch_id: Mapped[int | None] = mapped_column(ForeignKey("data_batch.id"))
    outcome_run_id: Mapped[int | None] = mapped_column(ForeignKey("outcome_run.id"))
    source_trade_date: Mapped[date] = mapped_column(Date)
    rule_version: Mapped[str] = mapped_column(String(32))
    horizon_trading_days: Mapped[int] = mapped_column(Integer)
    reference_trade_date: Mapped[date | None] = mapped_column(Date)
    evaluation_trade_date: Mapped[date | None] = mapped_column(Date)
    expected_evaluation_trade_date: Mapped[date | None] = mapped_column(Date)
    reference_price: Mapped[float | None] = mapped_column(Float)
    evaluation_price: Mapped[float | None] = mapped_column(Float)
    return_rate: Mapped[float | None] = mapped_column(Float)
    mfe: Mapped[float | None] = mapped_column(Float)
    mae: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    unavailable_reason: Mapped[str | None] = mapped_column(String(64))
    calculation_version: Mapped[str] = mapped_column(String(32), default="outcome-v1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class OutcomeRun(Base):
    __tablename__ = "outcome_run"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_batch_id",
            "calculation_version",
            "rule_version",
            "attempt_no",
            name="uq_outcome_run_batch_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_batch_id: Mapped[int] = mapped_column(ForeignKey("data_batch.id"))
    calculation_version: Mapped[str] = mapped_column(String(32), default="outcome-v1")
    rule_version: Mapped[str] = mapped_column(String(32))
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    expected_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    unavailable_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)


class WatchlistGroup(Base):
    __tablename__ = "watchlist_group"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class WatchlistItem(Base):
    __tablename__ = "watchlist_item"
    __table_args__ = (UniqueConstraint("market", "stock_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("watchlist_group.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))
    stock_code: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)


class AnalysisReport(Base):
    __tablename__ = "analysis_report"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "market",
            "stock_code",
            "trade_date",
            "rule_version",
            "template_version",
            "report_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("data_batch.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))
    stock_code: Mapped[str] = mapped_column(String(16))
    trade_date: Mapped[date] = mapped_column(Date)
    rule_version: Mapped[str] = mapped_column(String(32))
    template_version: Mapped[str] = mapped_column(String(32))
    report_version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


class ScreenerPreset(Base):
    __tablename__ = "screener_preset"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class DecisionNote(Base):
    __tablename__ = "decision_note"
    __table_args__ = (
        Index(
            "uq_active_decision_note",
            "market",
            "stock_code",
            "trade_date",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(8))
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RuleVersion(Base):
    __tablename__ = "rule_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    requires_recalculation: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AlertRuleVersion(Base):
    __tablename__ = "alert_rule_version"
    __table_args__ = (UniqueConstraint("logical_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    logical_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    rule_code: Mapped[str] = mapped_column(String(64))
    threshold: Mapped[float] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SystemSetting(Base):
    __tablename__ = "system_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class OperationLog(Base):
    __tablename__ = "operation_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_name: Mapped[str] = mapped_column(String(64), index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    page: Mapped[str] = mapped_column(String(64))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("data_batch.id"), index=True)
    market: Mapped[str | None] = mapped_column(String(8))
    stock_code: Mapped[str | None] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
