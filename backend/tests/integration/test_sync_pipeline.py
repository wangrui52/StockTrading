from collections.abc import Generator
from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.fake_market_data import FakeMarketDataGateway
from app.application.sync_pipeline import NonTradingDayError, SyncPipeline
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import (
    AlertRuleVersion,
    Base,
    CandidateResult,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    SignalEvent,
    StockBasic,
    SyncJob,
    TradeCalendar,
)
from app.main import recover_interrupted_jobs
from app.ports.market_data import MarketDataUnavailable, PriceRecord, StockRecord


def test_calendar_failure_is_recorded_and_not_left_pending(session_factory):
    class BrokenCalendar(FakeMarketDataGateway):
        def is_trade_date(self, value):
            raise MarketDataUnavailable("calendar offline")

    source = BrokenCalendar(set(), [], {})
    with pytest.raises(MarketDataUnavailable):
        SyncPipeline(session_factory, source).run(date(2026, 8, 27))
    with session_factory() as session:
        job = session.scalar(select(SyncJob))
        assert job.status == "FAILED"
        assert "calendar" in job.error_summary


def test_pending_job_without_batch_already_blocks_another_sync(session_factory):
    from app.application.sync_pipeline import SyncInProgressError

    with session_factory() as session:
        session.add(
            SyncJob(
                job_type="AUTO",
                target_trade_date=date(2026, 8, 27),
                status="PENDING",
                stage="PENDING",
            )
        )
        session.commit()
    with pytest.raises(SyncInProgressError):
        SyncPipeline(session_factory, gateway(date(2026, 8, 27))).prepare(date(2026, 8, 27))
    with session_factory() as session:
        assert session.scalar(select(func.count(SyncJob.id))) == 1


def test_concurrent_process_connections_claim_only_one_job(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.application.sync_pipeline import SyncInProgressError
    from app.infrastructure.database import create_sqlite_session_factory

    factory = create_sqlite_session_factory(f"sqlite+pysqlite:///{tmp_path / 'race.db'}")
    Base.metadata.create_all(factory.kw["bind"])
    entered, release = Event(), Event()
    target = date(2026, 8, 27)
    source = gateway(target)

    class SlowCalendar(FakeMarketDataGateway):
        def is_trade_date(self, value):
            entered.set()
            assert release.wait(3)
            return True

    source = SlowCalendar(source.open_dates, source.stocks, source.histories)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(SyncPipeline(factory, source).prepare, target)
        try:
            assert entered.wait(3)
            with pytest.raises(SyncInProgressError):
                SyncPipeline(factory, gateway(target)).prepare(target)
        finally:
            release.set()
        _, execute = first.result(timeout=3)
        assert execute
    with factory() as session:
        assert session.scalar(select(func.count(SyncJob.id))) == 1
        assert session.scalar(select(func.count(DataBatch.id))) == 1
    factory.kw["bind"].dispose()


def test_real_sync_never_merges_demo_history_and_updates_name(session_factory):
    target = date(2025, 3, 31)
    demo = gateway(target)
    demo.adapter_version = "demo-v1"
    demo.stocks[0] = replace(demo.stocks[0], name="示例股份001")
    old = SyncPipeline(session_factory, demo).run(target)
    real = gateway(target)
    real.adapter_version = "real-v1"
    real.histories = {code: rows[-5:] for code, rows in real.histories.items()}
    result = SyncPipeline(session_factory, real).run(target)
    assert result.batch_id != old.batch_id
    assert real.requested_start_dates == [None, None]
    with session_factory() as session:
        batch = session.get(DataBatch, result.batch_id)
        assert batch.source == "real-v1"
        assert (
            session.scalar(
                select(func.count(DailyPrice.id)).where(DailyPrice.batch_id == result.batch_id)
            )
            == 10
        )
        assert (
            session.scalar(select(StockBasic.stock_name).where(StockBasic.stock_code == "600000"))
            == "浦发银行"
        )


def test_stale_source_does_not_manufacture_today_suspended_bar(session_factory):
    target = date(2025, 3, 31)
    source = gateway(target)
    source.histories = {code: rows[:-1] for code, rows in source.histories.items()}
    result = SyncPipeline(session_factory, source).run(target)
    with session_factory() as session:
        batch = session.get(DataBatch, result.batch_id)
        assert batch.status == "FAILED"
        assert not batch.is_active
        assert not session.scalar(select(DailyPrice.id).where(DailyPrice.trade_date == target))


def test_large_gap_fetches_full_history_instead_of_ten_days(session_factory):
    old_target, target = date(2025, 3, 31), date(2026, 8, 27)
    SyncPipeline(session_factory, gateway(old_target)).run(old_target)
    source = gateway(target)
    SyncPipeline(session_factory, source).run(target)
    assert source.requested_start_dates == [None, None]


@pytest.mark.parametrize("qfq_revision", [False, True])
def test_incremental_sync_preserves_verified_historical_daily_changes(
    session_factory, qfq_revision
):
    yesterday = date(2026, 8, 26)
    first = gateway(yesterday)
    first.histories = {
        code: [replace(r, pct_change=6.0 if r.trade_date == yesterday else None) for r in rows]
        for code, rows in first.histories.items()
    }
    SyncPipeline(session_factory, first).run(yesterday)
    target = yesterday + timedelta(days=1)
    second = gateway(target)
    second.histories = {
        code: [
            replace(
                r,
                pct_change=1.0 if r.trade_date == target else None,
                open=r.open + (1 if qfq_revision else 0),
                high=r.high + (1 if qfq_revision else 0),
                low=r.low + (1 if qfq_revision else 0),
                close=r.close + (1 if qfq_revision else 0),
            )
            for r in first.histories[code] + [rows[-1]]
        ]
        for code, rows in second.histories.items()
    }
    result = SyncPipeline(session_factory, second).run(target)
    with session_factory() as session:
        change = session.scalar(
            select(DailyPrice.pct_change).where(
                DailyPrice.batch_id == result.batch_id, DailyPrice.trade_date == yesterday
            )
        )
        assert change == 6.0
        assert session.scalar(
            select(SignalEvent.id).where(
                SignalEvent.batch_id == result.batch_id,
                SignalEvent.trade_date == yesterday,
                SignalEvent.rule_code == "DAILY_SURGE",
            )
        )


def test_successful_stock_is_persisted_before_the_whole_pool_finishes(session_factory):
    target = date(2025, 3, 31)
    source = gateway(target)
    source.stocks = source.stocks * 3
    # 单线程时第二只股票开始之前必须已落盘第一只，避免全量历史驻留内存。
    original = source.daily_prices

    class StreamingSource(FakeMarketDataGateway):
        def daily_prices(self, stock, end_date, *, start_date=None):
            if stock.stock_code == "600001":
                with session_factory() as session:
                    assert session.scalar(select(func.count(DailyPrice.id))) > 0
            return original(stock, end_date, start_date=start_date)

    streaming = StreamingSource(source.open_dates, source.stocks[:2], source.histories)
    result = SyncPipeline(session_factory, streaming).run(target)
    with session_factory() as session:
        assert session.get(DataBatch, result.batch_id).status == "READY"


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
        assert session.scalar(select(TradeCalendar.is_open)) is True


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
        assert job.failed_items == ["600001"]
        assert job.retry_count == 2


def test_same_trade_date_sync_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    target = date(2025, 3, 31)
    pipeline = SyncPipeline(session_factory, gateway(target))
    first = pipeline.run(target)
    second = pipeline.run(target)

    with session_factory() as session:
        assert second.batch_id == first.batch_id
        assert session.scalar(select(func.count(DataBatch.id))) == 1
        assert session.scalar(select(func.count(DailyPrice.id))) == 130


def test_next_trade_date_fetches_incrementally_and_merges_revisions(
    session_factory: sessionmaker[Session],
) -> None:
    first_target = date(2025, 3, 31)
    next_target = date(2025, 4, 1)
    source = gateway(first_target)
    first = SyncPipeline(session_factory, source).run(first_target)

    for stock in source.stocks:
        history = source.histories[stock.stock_code]
        if stock.stock_code == "600000":
            history[-1] = replace(history[-1], close=99.0, high=100.0)
        history.append(
            PriceRecord(
                market=stock.market,
                stock_code=stock.stock_code,
                trade_date=next_target,
                open=99.0,
                high=101.0,
                low=98.0,
                close=100.0,
                volume=200_000,
                amount=2_000_000,
                pct_change=1.01,
                turnover_rate=2.5,
                adjustment="qfq",
            )
        )
    source.open_dates.add(next_target)

    second = SyncPipeline(session_factory, source).run(next_target)

    assert source.requested_start_dates[:2] == [None, None]
    assert source.requested_start_dates[2:] == [
        next_target - timedelta(days=10),
        None,  # 前复权修订时回拉完整历史，避免把旧因子尾部拼入新序列。
        next_target - timedelta(days=10),
    ]
    with session_factory() as session:
        old_close = session.scalar(
            select(DailyPrice.close).where(
                DailyPrice.batch_id == first.batch_id,
                DailyPrice.stock_code == "600000",
                DailyPrice.trade_date == first_target,
            )
        )
        revised_close = session.scalar(
            select(DailyPrice.close).where(
                DailyPrice.batch_id == second.batch_id,
                DailyPrice.stock_code == "600000",
                DailyPrice.trade_date == first_target,
            )
        )
        assert old_close != 99.0
        assert revised_close == 99.0
        assert (
            session.scalar(
                select(func.count(DailyPrice.id)).where(DailyPrice.batch_id == second.batch_id)
            )
            == 132
        )


def test_non_trading_day_does_not_create_batch(session_factory: sessionmaker[Session]) -> None:
    target = date(2025, 3, 30)
    source = FakeMarketDataGateway(open_dates=set(), stocks=[], histories={})

    with pytest.raises(NonTradingDayError):
        SyncPipeline(session_factory, source).run(target)

    with session_factory() as session:
        assert session.scalar(select(func.count(DataBatch.id))) == 0
        assert session.scalar(select(TradeCalendar.is_open)) is False
        job = session.scalar(select(SyncJob))
        assert job is not None and job.status == "FAILED"


def test_failed_trade_date_can_retry_into_a_new_ready_batch(
    session_factory: sessionmaker[Session],
) -> None:
    target = date(2025, 3, 31)
    failed = SyncPipeline(session_factory, gateway(target, failed_codes={"600001"})).run(target)
    retried = SyncPipeline(session_factory, gateway(target)).run(target)

    with session_factory() as session:
        assert retried.batch_id != failed.batch_id
        assert session.get(DataBatch, retried.batch_id).status == "READY"
        assert (
            session.scalar(
                select(func.count(DailyPrice.id)).where(DailyPrice.batch_id == retried.batch_id)
            )
            == 130
        )
        # 流式写入保留失败批次的已抓取数据，但该批次不激活。
        assert (
            session.scalar(
                select(func.count(DailyPrice.id)).where(DailyPrice.batch_id == failed.batch_id)
            )
            == 65
        )


def test_calculation_failure_is_recorded_and_keeps_previous_active_batch(
    session_factory: sessionmaker[Session],
) -> None:
    target = date(2025, 3, 31)
    with session_factory() as session:
        previous = DataBatch(
            trade_date=date(2025, 3, 28),
            status="READY",
            completeness_rate=1,
            rule_version="v1",
            is_active=True,
        )
        session.add(previous)
        session.commit()
        previous_id = previous.id

    pipeline = SyncPipeline(session_factory, gateway(target))

    class BrokenIndicators:
        def calculate(self, _bars: object) -> object:
            raise RuntimeError("golden sample failure")

    pipeline.indicators = BrokenIndicators()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="golden sample failure"):
        pipeline.run(target)

    with session_factory() as session:
        latest_job = session.scalar(select(SyncJob).order_by(SyncJob.id.desc()))
        latest_batch = session.scalar(select(DataBatch).order_by(DataBatch.id.desc()))
        assert latest_job is not None and latest_job.status == "FAILED"
        assert latest_job.stage == "CALCULATING"
        assert "RuntimeError" in latest_job.error_summary
        assert latest_batch is not None and latest_batch.status == "FAILED"
        assert session.get(DataBatch, previous_id).is_active


def test_enabled_custom_rule_generates_versioned_signal(
    session_factory: sessionmaker[Session],
) -> None:
    target = date(2025, 3, 31)
    with session_factory() as session:
        session.add(
            AlertRuleVersion(
                logical_id=1,
                version=1,
                name="RSI 自定义阈值",
                rule_code="CUSTOM_RSI",
                threshold=80,
                enabled=True,
            )
        )
        session.commit()

    SyncPipeline(session_factory, gateway(target)).run(target)

    with session_factory() as session:
        custom = session.scalar(select(SignalEvent).where(SignalEvent.rule_code == "CUSTOM_RSI"))
        assert custom is not None
        assert custom.payload["custom_rule_version"] == 1
        assert custom.payload["threshold"] == 80


def test_application_recovery_marks_interrupted_job_and_batch_failed(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        batch = DataBatch(
            trade_date=date(2025, 3, 31),
            status="BUILDING",
            completeness_rate=0,
            rule_version="v1",
            is_active=False,
        )
        session.add(batch)
        session.flush()
        job = SyncJob(
            batch_id=batch.id,
            job_type="MANUAL",
            target_trade_date=batch.trade_date,
            status="CALCULATING",
            stage="CALCULATING",
        )
        session.add(job)
        session.commit()
        job_id, batch_id = job.id, batch.id

    recover_interrupted_jobs(session_factory)

    with session_factory() as session:
        assert session.get(SyncJob, job_id).status == "FAILED"
        assert "应用进程中断" in session.get(SyncJob, job_id).error_summary
        assert session.get(DataBatch, batch_id).status == "FAILED"
