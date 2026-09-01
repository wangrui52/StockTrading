from collections.abc import Generator
from dataclasses import replace
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import app.application.sync_pipeline as sync_pipeline_application
import scripts.run_scheduler as scheduler_script
from app.adapters.fake_market_data import FakeMarketDataGateway
from app.application.candidate_outcomes import CandidateOutcomeModule
from app.application.sync_pipeline import NonTradingDayError, SyncPipeline
from app.infrastructure.database import create_sqlite_memory_session_factory
from app.infrastructure.models import (
    AlertRuleVersion,
    Base,
    CandidateResult,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    OutcomeRun,
    SignalEvent,
    StockBasic,
    SyncJob,
    TradeCalendar,
)
from app.main import recover_interrupted_jobs
from app.ports.market_data import (
    MarketDataUnavailable,
    PriceRecord,
    StockRecord,
    TradeCalendarRecord,
)


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


def test_successful_sync_persists_authoritative_calendar_for_price_history_range(
    session_factory: sessionmaker[Session],
) -> None:
    target = date(2025, 3, 31)
    source = gateway(target)
    missing_open_day = target - timedelta(days=2)
    history_dates = {
        item.trade_date
        for rows in source.histories.values()
        for item in rows
    }
    source.open_dates = history_dates - {missing_open_day}
    source.histories = {
        code: [item for item in rows if item.trade_date != missing_open_day]
        for code, rows in source.histories.items()
    }

    SyncPipeline(session_factory, source).run(target)

    with session_factory() as session:
        rows = session.execute(
            select(TradeCalendar.trade_date, TradeCalendar.is_open)
            .where(TradeCalendar.market == "CN")
            .order_by(TradeCalendar.trade_date)
        ).all()

    assert rows[0][0] == min(history_dates)
    assert rows[-1][0] == target
    assert len(rows) == (target - min(history_dates)).days + 1
    assert dict(rows)[missing_open_day] is False
    assert all(dict(rows)[value] is True for value in source.open_dates)


def test_legacy_provider_persists_each_natural_day_via_is_trade_date(
    session_factory: sessionmaker[Session],
) -> None:
    target = date(2025, 3, 31)
    source = gateway(target)
    history_start = min(
        item.trade_date
        for rows in source.histories.values()
        for item in rows
    )
    expected_dates = [
        history_start + timedelta(days=offset)
        for offset in range((target - history_start).days + 1)
    ]
    open_dates = set(expected_dates[::2]) | {target}
    calls: list[date] = []

    class LegacyProvider:
        adapter_version = source.adapter_version

        def is_trade_date(self, value: date) -> bool:
            calls.append(value)
            return value in open_dates

        def list_stocks(self):
            return source.list_stocks()

        def daily_prices(self, stock, end_date, *, start_date=None):
            return source.daily_prices(stock, end_date, start_date=start_date)

        def index_prices(self, end_date):
            return source.index_prices(end_date)

    pipeline = SyncPipeline(session_factory, LegacyProvider())  # type: ignore[arg-type]
    prepared, should_execute = pipeline.prepare(target)
    assert should_execute is True
    calls.clear()

    pipeline.execute_prepared(prepared.job_id, prepared.batch_id, target)

    assert calls == expected_dates
    with session_factory() as session:
        rows = session.execute(
            select(TradeCalendar.trade_date, TradeCalendar.is_open)
            .where(TradeCalendar.market == "CN")
            .order_by(TradeCalendar.trade_date)
        ).all()
    assert rows == [(value, value in open_dates) for value in expected_dates]


@pytest.mark.parametrize("missing_position", [0, 7, -1])
def test_incomplete_authoritative_calendar_is_rejected_before_activation_and_outcomes(
    session_factory: sessionmaker[Session],
    missing_position: int,
) -> None:
    target = date(2025, 3, 31)
    source = gateway(target)
    calendar_start = min(
        item.trade_date
        for rows in source.histories.values()
        for item in rows
    )
    all_dates = [
        calendar_start + timedelta(days=offset)
        for offset in range((target - calendar_start).days + 1)
    ]
    missing_date = all_dates[missing_position]
    outcome_calls: list[int] = []

    class IncompleteCalendarProvider(FakeMarketDataGateway):
        def trade_calendar(self, start_date: date, end_date: date):
            return [
                TradeCalendarRecord(value, value in self.open_dates)
                for value in all_dates
                if value != missing_date
            ]

    incomplete = IncompleteCalendarProvider(
        source.open_dates,
        source.stocks,
        source.histories,
    )
    pipeline = SyncPipeline(
        session_factory,
        incomplete,
        outcome_runner=outcome_calls.append,
    )

    with pytest.raises(MarketDataUnavailable, match="交易日历区间不完整"):
        pipeline.run(target)

    with session_factory() as session:
        batch = session.scalar(select(DataBatch).order_by(DataBatch.id.desc()))
        job = session.scalar(select(SyncJob).order_by(SyncJob.id.desc()))
        assert batch is not None and batch.status == "FAILED" and not batch.is_active
        assert job is not None and job.status == "FAILED"
        assert "交易日历区间不完整" in job.error_summary
        assert session.scalar(select(DataBatch.id).where(DataBatch.is_active.is_(True))) is None
    assert outcome_calls == []


def test_successful_sync_runs_outcomes_once_after_activation_commit(
    session_factory: sessionmaker[Session],
) -> None:
    target = date(2025, 3, 31)
    calls: list[int] = []

    def run_outcomes(batch_id: int) -> None:
        with session_factory() as session:
            batch = session.get(DataBatch, batch_id)
            job = session.scalar(select(SyncJob).where(SyncJob.batch_id == batch_id))
            assert batch is not None and batch.status == "READY" and batch.is_active
            assert job is not None and job.status == "READY"
        calls.append(batch_id)

    pipeline = SyncPipeline(
        session_factory,
        gateway(target),
        outcome_runner=run_outcomes,
    )
    first = pipeline.run(target)
    second = pipeline.run(target)

    assert first == second
    assert calls == [first.batch_id]


def test_run_scheduler_production_wiring_evaluates_new_active_ready_batch(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = date(2025, 3, 31)
    observed: list[int] = []
    recoveries: list[bool] = []

    def recover_outcomes(self: CandidateOutcomeModule) -> int:
        recoveries.append(True)
        return 0

    def evaluate_after_commit(
        self: CandidateOutcomeModule, batch_id: int
    ) -> object:
        with self._session_factory() as session:
            batch = session.get(DataBatch, batch_id)
            assert batch is not None
            assert batch.status == "READY"
            assert batch.is_active is True
        observed.append(batch_id)
        return object()

    class OneTickScheduler:
        def __init__(self, _factory, _is_trade_date, run_sync) -> None:
            self.run_sync = run_sync

        def tick(self, _now) -> bool:
            self.run_sync(target, job_type="AUTO")
            return True

    class StopLoop(Exception):
        pass

    def stop_loop(_seconds: int) -> None:
        raise StopLoop

    monkeypatch.setattr(
        scheduler_script, "create_sqlite_session_factory", lambda: session_factory
    )
    monkeypatch.setattr(
        scheduler_script, "TencentMarketDataGateway", lambda: gateway(target)
    )
    monkeypatch.setattr(scheduler_script, "DailySyncScheduler", OneTickScheduler)
    monkeypatch.setattr(
        CandidateOutcomeModule, "evaluate_due_outcomes", evaluate_after_commit
    )
    monkeypatch.setattr(
        CandidateOutcomeModule, "recover_interrupted_runs", recover_outcomes
    )
    monkeypatch.setattr(scheduler_script.time, "sleep", stop_loop)

    with pytest.raises(StopLoop):
        scheduler_script.main()

    assert recoveries == [True]
    assert len(observed) == 1
    with session_factory() as session:
        active = session.scalar(select(DataBatch).where(DataBatch.is_active.is_(True)))
        assert active is not None
        assert observed == [active.id]


def test_outcome_failure_does_not_change_successful_sync(
    session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = date(2025, 3, 31)
    sensitive_message = (
        "private-sync sqlite:////Users/private/sync.db "
        "SELECT * FROM candidate_outcome https://secret.example"
    )

    def fail_outcomes(_batch_id: int) -> None:
        raise RuntimeError(sensitive_message)

    pipeline = SyncPipeline(
        session_factory,
        gateway(target),
        outcome_runner=fail_outcomes,
    )
    prepared, should_execute = pipeline.prepare(target, job_type="AUTO")
    assert should_execute is True
    monkeypatch.setattr(sync_pipeline_application.logger, "disabled", False)
    with caplog.at_level("ERROR", logger="app.application.sync_pipeline"):
        result = pipeline.execute_prepared(
            prepared.job_id,
            prepared.batch_id,
            target,
        )
    assert result == prepared
    logs = caplog.text
    assert f"batch_id={prepared.batch_id}" in logs
    assert "error_type=RuntimeError" in logs
    assert "private-sync" not in logs
    assert "SELECT" not in logs
    assert "/Users/private" not in logs
    assert "https://secret.example" not in logs

    with session_factory() as session:
        batch = session.get(DataBatch, result.batch_id)
        job = session.get(SyncJob, result.job_id)
        assert batch is not None and batch.status == "READY" and batch.is_active
        assert job is not None and job.status == "READY" and job.stage == "READY"
        assert job.job_type == "AUTO"


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


def test_application_recovery_commits_sync_state_when_outcome_table_is_missing() -> None:
    old_schema_factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(
        old_schema_factory.kw["bind"],
        tables=[DataBatch.__table__, SyncJob.__table__],
    )
    with old_schema_factory() as session:
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
            status="FETCHING",
            stage="FETCHING",
        )
        session.add(job)
        ready_batch = DataBatch(
            trade_date=date(2025, 3, 28),
            status="READY",
            completeness_rate=1,
            rule_version="v1",
            is_active=False,
        )
        session.add(ready_batch)
        session.flush()
        completed_job = SyncJob(
            batch_id=ready_batch.id,
            job_type="MANUAL",
            target_trade_date=ready_batch.trade_date,
            status="COMPLETED",
            stage="COMPLETED",
        )
        session.add(completed_job)
        session.commit()
        job_id, batch_id = job.id, batch.id
        completed_job_id, ready_batch_id = completed_job.id, ready_batch.id

    recover_interrupted_jobs(old_schema_factory)

    with old_schema_factory() as session:
        assert session.get(SyncJob, job_id).status == "FAILED"
        assert session.get(DataBatch, batch_id).status == "FAILED"
        assert session.get(SyncJob, completed_job_id).status == "COMPLETED"
        assert session.get(DataBatch, ready_batch_id).status == "READY"

    old_schema_factory.kw["bind"].dispose()


def test_application_recovery_continues_when_sync_table_is_missing() -> None:
    outcome_only_factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(
        outcome_only_factory.kw["bind"],
        tables=[DataBatch.__table__, OutcomeRun.__table__],
    )
    with outcome_only_factory() as session:
        batch = DataBatch(
            trade_date=date(2025, 3, 31),
            status="READY",
            completeness_rate=1,
            rule_version="v1",
            is_active=False,
        )
        session.add(batch)
        session.flush()
        run = OutcomeRun(
            evaluation_batch_id=batch.id,
            rule_version=batch.rule_version,
            status="RUNNING",
        )
        session.add(run)
        session.commit()
        run_id = run.id

    recover_interrupted_jobs(outcome_only_factory)

    with outcome_only_factory() as session:
        recovered = session.get(OutcomeRun, run_id)
        assert recovered.status == "FAILED"
        assert recovered.finished_at is not None
        assert "可重试" in recovered.error_summary

    outcome_only_factory.kw["bind"].dispose()


def test_application_recovery_only_fails_running_outcome_runs(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        batches = [
            DataBatch(
                trade_date=date(2025, 3, 28 + offset),
                status="READY",
                completeness_rate=1,
                rule_version="v1",
                is_active=False,
            )
            for offset in range(4)
        ]
        session.add_all(batches)
        session.flush()
        runs = [
            OutcomeRun(
                evaluation_batch_id=batch.id,
                rule_version=batch.rule_version,
                status=status,
            )
            for batch, status in zip(
                batches,
                ("RUNNING", "PENDING", "COMPLETED", "FAILED"),
                strict=True,
            )
        ]
        session.add_all(runs)
        session.commit()
        run_ids = [run.id for run in runs]

    recover_interrupted_jobs(session_factory)

    with session_factory() as session:
        recovered = [session.get(OutcomeRun, run_id) for run_id in run_ids]
        assert [run.status for run in recovered] == [
            "FAILED",
            "PENDING",
            "COMPLETED",
            "FAILED",
        ]
        assert recovered[0].finished_at is not None
        assert "应用进程中断" in recovered[0].error_summary
        assert recovered[1].finished_at is None


def test_application_recovery_without_migrated_tables_does_not_block_startup() -> None:
    empty_factory = create_sqlite_memory_session_factory()

    recover_interrupted_jobs(empty_factory)

    empty_factory.kw["bind"].dispose()
