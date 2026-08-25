from datetime import date, datetime
from typing import Any, Protocol

from app.infrastructure.models import AlertEventState, AnalysisReport, DataBatch, SignalEvent


class BatchStore(Protocol):
    def activate_ready_batch(self, batch_id: int) -> DataBatch: ...


class SignalStore(Protocol):
    def record_signal(
        self,
        *,
        batch_id: int,
        market: str,
        stock_code: str,
        trade_date: date,
        rule_code: str,
        rule_version: str,
        payload: dict[str, Any],
    ) -> SignalEvent: ...

    def confirm(self, signal_event_id: int, *, confirmed_at: datetime) -> AlertEventState: ...


class ReportStore(Protocol):
    def create_report(
        self,
        *,
        batch_id: int,
        market: str,
        stock_code: str,
        trade_date: date,
        rule_version: str,
        template_version: str,
        content: str,
    ) -> AnalysisReport: ...
