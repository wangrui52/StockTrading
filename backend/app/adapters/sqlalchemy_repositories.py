from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.infrastructure.models import AlertEventState, AnalysisReport, DataBatch, SignalEvent


class BatchNotReadyError(ValueError):
    pass


class SQLAlchemyBatchStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def activate_ready_batch(self, batch_id: int) -> DataBatch:
        batch = self.session.get(DataBatch, batch_id)
        if batch is None or batch.status not in {"READY", "READY_WITH_GAPS"}:
            raise BatchNotReadyError(f"batch {batch_id} is not ready")
        self.session.execute(
            update(DataBatch).where(DataBatch.is_active.is_(True)).values(is_active=False)
        )
        batch.is_active = True
        batch.activated_at = datetime.now(UTC)
        self.session.flush()
        return batch


class SQLAlchemySignalStore:
    def __init__(self, session: Session) -> None:
        self.session = session

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
    ) -> SignalEvent:
        existing = self.session.scalar(
            select(SignalEvent).where(
                SignalEvent.batch_id == batch_id,
                SignalEvent.market == market,
                SignalEvent.stock_code == stock_code,
                SignalEvent.trade_date == trade_date,
                SignalEvent.rule_code == rule_code,
                SignalEvent.rule_version == rule_version,
            )
        )
        if existing is not None:
            return existing
        signal = SignalEvent(
            batch_id=batch_id,
            market=market,
            stock_code=stock_code,
            trade_date=trade_date,
            rule_code=rule_code,
            rule_version=rule_version,
            payload=payload,
        )
        self.session.add(signal)
        self.session.flush()
        batch = self.session.get(DataBatch, batch_id)
        confirmed = (
            self.session.scalar(
                select(AlertEventState)
                .join(SignalEvent, SignalEvent.id == AlertEventState.signal_event_id)
                .join(DataBatch, DataBatch.id == SignalEvent.batch_id)
                .where(
                    DataBatch.source == batch.source,
                    DataBatch.status.in_(("READY", "READY_WITH_GAPS")),
                    DataBatch.id != batch_id,
                    SignalEvent.market == market,
                    SignalEvent.stock_code == stock_code,
                    SignalEvent.trade_date == trade_date,
                    SignalEvent.rule_code == rule_code,
                    SignalEvent.rule_version == rule_version,
                    AlertEventState.status == "CONFIRMED",
                )
                .order_by(DataBatch.id.desc())
            )
            if batch is not None
            else None
        )
        self.session.add(
            AlertEventState(
                signal_event_id=signal.id,
                status="CONFIRMED" if confirmed else "TRIGGERED",
                confirmed_at=confirmed.confirmed_at if confirmed else None,
            )
        )
        self.session.flush()
        return signal

    def confirm(self, signal_event_id: int, *, confirmed_at: datetime) -> AlertEventState:
        state = self.session.scalar(
            select(AlertEventState).where(AlertEventState.signal_event_id == signal_event_id)
        )
        if state is None:
            state = AlertEventState(signal_event_id=signal_event_id)
            self.session.add(state)
        state.status = "CONFIRMED"
        state.confirmed_at = confirmed_at
        self.session.flush()
        return state


class SQLAlchemyReportStore:
    def __init__(self, session: Session) -> None:
        self.session = session

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
    ) -> AnalysisReport:
        latest = self.session.scalar(
            select(func.max(AnalysisReport.report_version)).where(
                AnalysisReport.batch_id == batch_id,
                AnalysisReport.market == market,
                AnalysisReport.stock_code == stock_code,
                AnalysisReport.trade_date == trade_date,
                AnalysisReport.rule_version == rule_version,
                AnalysisReport.template_version == template_version,
            )
        )
        report = AnalysisReport(
            batch_id=batch_id,
            market=market,
            stock_code=stock_code,
            trade_date=trade_date,
            rule_version=rule_version,
            template_version=template_version,
            report_version=(latest or 0) + 1,
            content=content,
        )
        self.session.add(report)
        self.session.flush()
        return report
