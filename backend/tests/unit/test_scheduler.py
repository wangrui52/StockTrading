from datetime import UTC, date, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.application.scheduler import DailySyncScheduler
from app.application.sync_pipeline import SyncResult
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import Base, SyncJob, SystemSetting


def factory() -> sessionmaker[Session]:
    value = create_sqlite_memory_session_factory()
    Base.metadata.create_all(value.kw["bind"])
    return value


def test_scheduler_runs_once_after_configured_time_on_trade_date() -> None:
    session_factory = factory()
    calls: list[tuple[date, str]] = []

    def run(target: date, *, job_type: str) -> SyncResult:
        calls.append((target, job_type))
        with session_factory() as session:
            session.add(
                SyncJob(
                    job_type=job_type,
                    target_trade_date=target,
                    status="PENDING",
                    stage="PENDING",
                )
            )
            session.commit()
        return SyncResult(job_id=1, batch_id=1)

    scheduler = DailySyncScheduler(session_factory, lambda _target: True, run)
    before = datetime(2025, 3, 31, 10, 29, tzinfo=UTC)
    after = datetime(2025, 3, 31, 10, 30, tzinfo=UTC)

    assert scheduler.tick(before) is False
    assert scheduler.tick(after) is True
    assert scheduler.tick(after) is False
    assert calls == [(date(2025, 3, 31), "AUTO")]


def test_scheduler_respects_disabled_setting_and_non_trading_day() -> None:
    session_factory = factory()
    with session_factory() as session:
        session.add(
            SystemSetting(
                key="application",
                value={"auto_sync_enabled": False, "auto_sync_time": "18:30"},
            )
        )
        session.commit()
    calls: list[date] = []
    scheduler = DailySyncScheduler(
        session_factory,
        lambda _target: False,
        lambda target, **_kwargs: calls.append(target),  # type: ignore[arg-type]
    )

    assert scheduler.tick(datetime(2025, 3, 31, 11, 0, tzinfo=UTC)) is False
    assert calls == []
