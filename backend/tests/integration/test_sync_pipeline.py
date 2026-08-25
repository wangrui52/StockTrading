from collections.abc import Generator
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.fake_market_data import FakeMarketDataGateway
from app.application.sync_pipeline import NonTradingDayError, SyncPipeline
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import (
    Base,
    CandidateResult,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    SignalEvent,
    SyncJob,
)
from app.ports.market_data import PriceRecord, StockRecord


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session]]:
    factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(factory.kw["bind"])
    yield factory
    factory.kw["bind"].dispose()


def prices(code: str, end_date: date, days: int = 65) -> list[PriceRecord]:
    first = end_date - timedelta(days=days - 1)
    return [
        PriceRecord(
            market="SH",
            stock_code=code,
            trade_date=first + timedelta(days=index),
            open=10 + index * 0.1,
            high=10.3 + index * 0.1,
            low=9.8 + index * 0.1,
            close=10.2 + index * 0.1,
            volume=100_000 + index * 1_000,
            amount=1_000_000 + index * 10_000,
            pct_change=1.0,
            turnover_rate=2.0,
            adjustment="qfq",
        )
        for index in range(days)
    ]


def gateway(target: date, *, failed_codes: set[str] | None = None) -> FakeMarketDataGateway:
    stocks = [
        StockRecord("SH", "600000", "浦发银行", "银行", date(1999, 11, 10), False),
        StockRecord("SH", "600001", "示例股份", "制造", date(2000, 1, 1), False),
    ]
    histories = {stock.stock_code: prices(stock.stock_code, target) for stock in stocks}
    return FakeMarketDataGateway(
        open_dates={target}, stocks=stocks, histories=histories, failed_codes=failed_codes or set()
    )


def test_successful_sync_calculates_and_activates_complete_batch(
    session_factory: sessionmaker[Session],
) -> None:
    target = date(2025, 3, 31)
    result = SyncPipeline(session_factory, gateway(target)).run(target)

    with session_factory() as session:
        batch = session.get(DataBatch, result.batch_id)
        job = session.get(SyncJob, result.job_id)
        assert batch is not None and batch.status == "READY" and batch.is_active
        assert batch.completeness_rate == 1.0
        assert job is not None and job.status == "READY" and job.stage == "READY"
        assert session.scalar(select(func.count(DailyPrice.id))) == 130
        assert session.scalar(select(func.count(DailyIndicator.id))) == 130
        assert session.scalar(select(func.count(SignalEvent.id))) >= 0
        assert session.scalar(select(func.count(CandidateResult.id))) >= 0


def test_partial_failure_marks_batch_failed_and_preserves_previous_active(
    session_factory: sessionmaker[Session],
) -> None:
    target = date(2025, 3, 31)
    with session_factory() as session:
        previous = DataBatch(
            trade_date=date(2025, 3, 28),
            status="READY",
            completeness_rate=1.0,
            rule_version="v1",
            is_active=True,
        )
        session.add(previous)
        session.commit()
        previous_id = previous.id

    result = SyncPipeline(session_factory, gateway(target, failed_codes={"600001"})).run(target)

    with session_factory() as session:
        failed = session.get(DataBatch, result.batch_id)
        previous = session.get(DataBatch, previous_id)
        job = session.get(SyncJob, result.job_id)
        assert failed is not None and failed.status == "FAILED" and not failed.is_active
        assert failed.completeness_rate == 0.5
        assert previous is not None and previous.is_active
        assert job is not None and job.status == "FAILED"
        assert job.failed_count == 1


def test_same_trade_date_sync_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    target = date(2025, 3, 31)
    pipeline = SyncPipeline(session_factory, gateway(target))
    first = pipeline.run(target)
    second = pipeline.run(target)

    with session_factory() as session:
        assert second.batch_id == first.batch_id
        assert session.scalar(select(func.count(DataBatch.id))) == 1
        assert session.scalar(select(func.count(DailyPrice.id))) == 130


def test_non_trading_day_does_not_create_batch(session_factory: sessionmaker[Session]) -> None:
    target = date(2025, 3, 30)
    source = FakeMarketDataGateway(open_dates=set(), stocks=[], histories={})

    with pytest.raises(NonTradingDayError):
        SyncPipeline(session_factory, source).run(target)

    with session_factory() as session:
        assert session.scalar(select(func.count(DataBatch.id))) == 0
        job = session.scalar(select(SyncJob))
        assert job is not None and job.status == "FAILED"
