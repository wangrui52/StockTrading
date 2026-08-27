from collections.abc import Generator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.sqlalchemy_repositories import (
    BatchNotReadyError,
    SQLAlchemyBatchStore,
    SQLAlchemyReportStore,
    SQLAlchemySignalStore,
)
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import (
    AlertEventState,
    AnalysisReport,
    Base,
    DailyPrice,
    DataBatch,
    SignalEvent,
)


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session]]:
    factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(factory.kw["bind"])
    yield factory
    factory.kw["bind"].dispose()


def ready_batch(trade_date: date, *, active: bool = False) -> DataBatch:
    return DataBatch(
        trade_date=trade_date,
        status="READY",
        completeness_rate=1.0,
        rule_version="v1",
        is_active=active,
    )


def test_failed_or_demo_batch_signals_do_not_leak_into_successful_retry(session_factory):
    with session_factory() as session:
        first = ready_batch(date(2026, 8, 27))
        first.status, first.source = "FAILED", "demo-v1"
        second = ready_batch(first.trade_date)
        second.source = "tencent-sina-v1"
        session.add_all([first, second])
        session.commit()
        store = SQLAlchemySignalStore(session)
        args = dict(
            market="SH",
            stock_code="600000",
            trade_date=first.trade_date,
            rule_code="DAILY_DROP",
            rule_version="v1",
        )
        old = store.record_signal(batch_id=first.id, payload={"fake": True}, **args)
        store.confirm(old.id, confirmed_at=datetime.now(UTC))
        current = store.record_signal(batch_id=second.id, payload={"fake": False}, **args)
        session.commit()
        assert current.id != old.id
        assert current.batch_id == second.id and current.payload == {"fake": False}
        state = session.scalar(
            select(AlertEventState).where(AlertEventState.signal_event_id == current.id)
        )
        assert state.status == "TRIGGERED"


@pytest.mark.parametrize("previous_status", ["READY", "READY_WITH_GAPS"])
def test_confirmed_real_signal_keeps_confirmation_across_ready_batches(
    session_factory, previous_status
):
    with session_factory() as session:
        first, second = ready_batch(date(2026, 8, 26)), ready_batch(date(2026, 8, 27))
        first.source = second.source = "tencent-sina-v1"
        first.status = previous_status
        session.add_all([first, second])
        session.commit()
        store = SQLAlchemySignalStore(session)
        args = dict(
            market="SH",
            stock_code="600000",
            trade_date=first.trade_date,
            rule_code="DAILY_DROP",
            rule_version="v1",
            payload={},
        )
        old = store.record_signal(batch_id=first.id, **args)
        store.confirm(old.id, confirmed_at=datetime.now(UTC))
        new = store.record_signal(batch_id=second.id, **args)
        assert old.id != new.id
        state = session.scalar(
            select(AlertEventState).where(AlertEventState.signal_event_id == new.id)
        )
        assert state.status == "CONFIRMED"


def test_daily_price_unique_key_rejects_duplicate_market_date_and_adjustment(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        batch = ready_batch(date(2025, 1, 2))
        session.add(batch)
        session.flush()
        price = dict(
            batch_id=batch.id,
            market="SZ",
            stock_code="000001",
            trade_date=batch.trade_date,
            adjustment="raw",
            open=10.0,
            high=10.5,
            low=9.8,
            close=10.2,
            volume=100_000,
            amount=1_020_000.0,
            pct_change=2.0,
        )
        session.add(DailyPrice(**price))
        session.commit()
        session.add(DailyPrice(**price))

        with pytest.raises(IntegrityError):
            session.commit()


def test_activate_batch_switches_ready_batch_atomically(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        previous = ready_batch(date(2025, 1, 2), active=True)
        current = ready_batch(date(2025, 1, 3))
        session.add_all([previous, current])
        session.commit()

        SQLAlchemyBatchStore(session).activate_ready_batch(current.id)
        session.commit()

        session.refresh(previous)
        session.refresh(current)
        assert previous.is_active is False
        assert current.is_active is True
        assert current.activated_at is not None


def test_failed_batch_cannot_replace_current_active_batch(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        current = ready_batch(date(2025, 1, 2), active=True)
        failed = DataBatch(
            trade_date=date(2025, 1, 3),
            status="FAILED",
            completeness_rate=0.7,
            rule_version="v1",
            is_active=False,
        )
        session.add_all([current, failed])
        session.commit()

        with pytest.raises(BatchNotReadyError):
            SQLAlchemyBatchStore(session).activate_ready_batch(failed.id)

        session.refresh(current)
        assert current.is_active is True


def test_recording_same_signal_is_idempotent_and_keeps_confirmation(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        batch = ready_batch(date(2025, 1, 2))
        session.add(batch)
        session.commit()
        store = SQLAlchemySignalStore(session)

        first = store.record_signal(
            batch_id=batch.id,
            market="SH",
            stock_code="600000",
            trade_date=batch.trade_date,
            rule_code="MACD_GOLDEN_CROSS",
            rule_version="v1",
            payload={"dif": 0.2, "dea": 0.1},
        )
        session.commit()
        store.confirm(first.id, confirmed_at=datetime.now(UTC))
        session.commit()

        repeated = store.record_signal(
            batch_id=batch.id,
            market="SH",
            stock_code="600000",
            trade_date=batch.trade_date,
            rule_code="MACD_GOLDEN_CROSS",
            rule_version="v1",
            payload={"dif": 0.2, "dea": 0.1},
        )
        session.commit()

        state = session.scalar(
            select(AlertEventState).where(AlertEventState.signal_event_id == first.id)
        )
        assert repeated.id == first.id
        assert state is not None
        assert state.status == "CONFIRMED"


def test_report_regeneration_creates_next_version_without_overwrite(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        batch = ready_batch(date(2025, 1, 2))
        session.add(batch)
        session.commit()
        store = SQLAlchemyReportStore(session)

        first = store.create_report(
            batch_id=batch.id,
            market="SH",
            stock_code="600000",
            trade_date=batch.trade_date,
            rule_version="v1",
            template_version="v1",
            content="first",
        )
        second = store.create_report(
            batch_id=batch.id,
            market="SH",
            stock_code="600000",
            trade_date=batch.trade_date,
            rule_version="v1",
            template_version="v1",
            content="second",
        )
        session.commit()

        assert (first.report_version, second.report_version) == (1, 2)
        assert first.content == "first"
        assert second.content == "second"
        assert session.scalar(select(func.count(AnalysisReport.id))) == 2
        assert session.scalar(select(func.count(SignalEvent.id))) == 0
