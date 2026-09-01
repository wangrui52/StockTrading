from concurrent.futures import ThreadPoolExecutor
from datetime import date
from multiprocessing import get_context
from queue import Empty
from threading import Event

import pytest
from sqlalchemy import event, func, select

import app.application.candidate_outcomes as outcome_application
from app.application.candidate_outcomes import (
    CandidateOutcomeModule,
    CandidateOutcomeNotFoundError,
    OutcomeBatchNotReadyError,
    OutcomeFilters,
    OutcomeRunStateError,
    UnsupportedOutcomeEvaluationBackendError,
)
from app.infrastructure.database import (
    create_sqlite_memory_session_factory,
    create_sqlite_session_factory,
)
from app.infrastructure.models import (
    Base,
    CandidateOutcome,
    CandidateResult,
    DailyPrice,
    DataBatch,
    OutcomeRun,
    StockBasic,
    TradeCalendar,
)


@pytest.fixture
def session_factory():
    factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(factory.kw["bind"])
    yield factory
    factory.kw["bind"].dispose()


def add_batch(
    session,
    trade_date: date,
    *,
    status: str = "READY",
    active: bool = False,
    rule_version: str = "rules-v1",
    source: str = "test",
):
    batch = DataBatch(
        source=source,
        trade_date=trade_date,
        status=status,
        completeness_rate=1.0,
        rule_version=rule_version,
        is_active=active,
    )
    session.add(batch)
    session.flush()
    return batch


def add_candidate(session, batch: DataBatch, market: str, code: str):
    candidate = CandidateResult(
        batch_id=batch.id,
        market=market,
        stock_code=code,
        score=80,
        reasons=[],
        positive_event_count=1,
    )
    session.add(candidate)
    session.flush()
    return candidate


def add_completed_run(session, batch: DataBatch) -> OutcomeRun:
    run = OutcomeRun(
        evaluation_batch_id=batch.id,
        calculation_version="outcome-v1",
        rule_version=batch.rule_version,
        status="COMPLETED",
    )
    session.add(run)
    session.flush()
    return run


def add_price(
    session,
    batch_id: int,
    market: str,
    code: str,
    trade_date: date,
    *,
    adjustment: str = "raw",
    open_price: float = 10,
    high: float = 11,
    low: float = 9,
    close: float = 10,
    volume: int = 100,
    suspended: bool = False,
):
    session.add(
        DailyPrice(
            batch_id=batch_id,
            market=market,
            stock_code=code,
            trade_date=trade_date,
            adjustment=adjustment,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            amount=1000,
            is_suspended=suspended,
        )
    )


def evaluate_in_separate_process(
    database_url,
    evaluation_batch_id,
    call_started,
    entered_evaluate,
    release_evaluate,
    results,
):
    factory = create_sqlite_session_factory(database_url)
    module = CandidateOutcomeModule(factory)
    original_evaluate = module._evaluate_candidate_chunk

    def instrumented_evaluate(session, batch_id, after_candidate_id, **kwargs):
        entered_evaluate.set()
        if release_evaluate is not None:
            assert release_evaluate.wait(timeout=10)
        return original_evaluate(session, batch_id, after_candidate_id, **kwargs)

    module._evaluate_candidate_chunk = instrumented_evaluate
    call_started.set()
    try:
        run = module.evaluate_due_outcomes(evaluation_batch_id)
    except Exception as error:
        results.put(("error", type(error).__name__))
    else:
        results.put(("ok", run.status))
    finally:
        factory.kw["bind"].dispose()


def hold_outcome_lock_in_separate_process(
    database_url,
    lock_acquired,
    release_lock,
):
    factory = create_sqlite_session_factory(database_url)
    module = CandidateOutcomeModule(factory)
    try:
        with module._evaluation_guard():
            lock_acquired.set()
            assert release_lock.wait(timeout=10)
    finally:
        factory.kw["bind"].dispose()


def seed_calendar(session):
    dates = [
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    session.add_all(
        [TradeCalendar(market="CN", trade_date=value, is_open=True) for value in dates]
    )
    session.add_all(
        [
            TradeCalendar(
                market="CN",
                trade_date=value,
                is_open=False,
            )
            for value in (date(2026, 8, 29), date(2026, 8, 30))
        ]
    )
    return dates


def test_evaluates_trading_day_horizons_and_keeps_not_due_pending(session_factory):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 1), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for index, trade_date in enumerate(dates[:3]):
            add_price(
                session,
                evaluation.id,
                "SH",
                "600000",
                trade_date,
                open_price=10 + index,
                high=11 + index,
                low=9 + index,
                close=10.5 + index,
            )
        session.commit()

    result = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)

    assert (result.expected_count, result.completed_count, result.pending_count) == (3, 2, 1)
    outcomes = CandidateOutcomeModule(session_factory).get_candidate_outcomes(candidate.id)
    assert [item.status for item in outcomes] == ["COMPLETED", "COMPLETED", "PENDING"]
    assert outcomes[0].reference_trade_date == date(2026, 8, 28)
    assert outcomes[1].evaluation_trade_date == date(2026, 9, 1)
    assert outcomes[1].return_rate == pytest.approx(25.0)
    assert outcomes[1].mfe == pytest.approx(30.0)
    assert outcomes[1].mae == pytest.approx(-10.0)
    assert outcomes[2].evaluation_trade_date is None
    assert outcomes[2].expected_evaluation_trade_date == date(2026, 9, 3)


def test_evaluation_does_not_infer_horizons_from_raw_prices_when_calendar_is_sparse(
    session_factory,
):
    trading_dates = [
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    with session_factory() as session:
        source = add_batch(
            session,
            date(2026, 8, 27),
            source="tencent-sina-v1",
        )
        evaluation = add_batch(
            session,
            trading_dates[-1],
            active=True,
            source="tencent-sina-v1",
        )
        candidate = add_candidate(session, source, "SH", "600000")
        session.add(
            TradeCalendar(
                market="CN",
                trade_date=trading_dates[-1],
                is_open=True,
            )
        )
        for trade_date in trading_dates:
            add_price(session, evaluation.id, "SH", "600000", trade_date)
        session.commit()

    run = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    outcomes = CandidateOutcomeModule(session_factory).get_candidate_outcomes(candidate.id)

    assert (run.expected_count, run.completed_count, run.pending_count) == (3, 0, 3)
    assert [item.status for item in outcomes] == ["PENDING", "PENDING", "PENDING"]
    assert all(item.reference_trade_date is None for item in outcomes)
    assert all(item.evaluation_trade_date is None for item in outcomes)
    assert all(item.expected_evaluation_trade_date is None for item in outcomes)


def test_evaluation_keeps_affected_horizons_pending_when_all_prices_miss_open_day(
    session_factory,
):
    trading_dates = [
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    missing_date = trading_dates[1]
    with session_factory() as session:
        source = add_batch(session, date(2026, 8, 27), source="tencent-sina-v1")
        evaluation = add_batch(
            session,
            trading_dates[-1],
            active=True,
            source="tencent-sina-v1",
        )
        candidate = add_candidate(session, source, "SH", "600000")
        session.add_all(
            [
                TradeCalendar(market="CN", trade_date=value, is_open=True)
                for value in trading_dates
            ]
            + [
                TradeCalendar(
                    market="CN",
                    trade_date=value,
                    is_open=False,
                )
                for value in (date(2026, 8, 29), date(2026, 8, 30))
            ]
        )
        for trade_date in trading_dates:
            if trade_date != missing_date:
                add_price(session, evaluation.id, "SH", "600000", trade_date)
        session.commit()

    run = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    outcomes = CandidateOutcomeModule(session_factory).get_candidate_outcomes(candidate.id)

    assert (run.expected_count, run.completed_count, run.pending_count) == (3, 1, 2)
    assert [item.status for item in outcomes] == ["COMPLETED", "PENDING", "PENDING"]
    assert outcomes[0].evaluation_trade_date == trading_dates[0]
    assert outcomes[1].evaluation_trade_date is None
    assert outcomes[2].evaluation_trade_date is None
    assert outcomes[1].expected_evaluation_trade_date == trading_dates[2]
    assert outcomes[2].expected_evaluation_trade_date == trading_dates[4]


def test_evaluation_keeps_horizon_pending_when_raw_history_is_too_short(
    session_factory,
):
    available_dates = [
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
    ]
    with session_factory() as session:
        source = add_batch(
            session,
            date(2026, 8, 27),
            source="tencent-sina-v1",
        )
        evaluation = add_batch(
            session,
            date(2026, 9, 3),
            active=True,
            source="tencent-sina-v1",
        )
        candidate = add_candidate(session, source, "SH", "600000")
        seed_calendar(session)
        for trade_date in available_dates:
            add_price(session, evaluation.id, "SH", "600000", trade_date)
        session.commit()

    run = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    outcomes = CandidateOutcomeModule(session_factory).get_candidate_outcomes(candidate.id)

    assert (run.expected_count, run.completed_count, run.pending_count) == (3, 2, 1)
    assert [item.status for item in outcomes] == ["COMPLETED", "COMPLETED", "PENDING"]
    assert outcomes[-1].evaluation_trade_date is None
    assert outcomes[-1].expected_evaluation_trade_date == date(2026, 9, 3)


def test_pending_expected_date_is_preserved_by_idempotency_and_later_snapshot(
    session_factory,
):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        early_evaluation = add_batch(session, date(2026, 9, 1), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for trade_date in dates[:3]:
            add_price(
                session,
                early_evaluation.id,
                candidate.market,
                candidate.stock_code,
                trade_date,
            )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    early_run = module.evaluate_due_outcomes(early_evaluation.id)
    repeated = module.evaluate_due_outcomes(early_evaluation.id)
    early_pending = module.query_outcomes(
        OutcomeFilters(horizon=5, status="PENDING")
    ).items[0]

    assert repeated.id == early_run.id
    assert early_pending.evaluation_trade_date is None
    assert early_pending.expected_evaluation_trade_date == date(2026, 9, 3)

    with session_factory() as session:
        session.get(DataBatch, early_evaluation.id).is_active = False
        later_evaluation = add_batch(session, date(2026, 9, 3), active=True)
        for trade_date in dates:
            add_price(
                session,
                later_evaluation.id,
                candidate.market,
                candidate.stock_code,
                trade_date,
            )
        session.commit()

    module.evaluate_due_outcomes(later_evaluation.id)
    published = module.query_outcomes(OutcomeFilters(horizon=5)).items[0]

    assert published.status == "COMPLETED"
    assert published.evaluation_trade_date == date(2026, 9, 3)
    assert published.expected_evaluation_trade_date == date(2026, 9, 3)
    with session_factory() as session:
        snapshots = session.scalars(
            select(CandidateOutcome)
            .where(
                CandidateOutcome.candidate_result_id == candidate.id,
                CandidateOutcome.horizon_trading_days == 5,
            )
            .order_by(CandidateOutcome.id)
        ).all()
        assert [item.status for item in snapshots] == ["PENDING", "COMPLETED"]
        assert {
            item.expected_evaluation_trade_date for item in snapshots
        } == {date(2026, 9, 3)}


def test_retry_creates_new_snapshot_when_calendar_now_reveals_expected_dates(
    session_factory,
):
    with session_factory() as session:
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 1), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for trade_date in (
            date(2026, 8, 28),
            date(2026, 8, 31),
            date(2026, 9, 1),
        ):
            add_price(
                session,
                evaluation.id,
                candidate.market,
                candidate.stock_code,
                trade_date,
            )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    first_run = module.evaluate_due_outcomes(evaluation.id)
    first_snapshot = module.query_outcomes(OutcomeFilters()).items
    assert all(item.status == "PENDING" for item in first_snapshot)
    assert all(
        item.expected_evaluation_trade_date is None for item in first_snapshot
    )

    with session_factory() as session:
        seed_calendar(session)
        session.commit()

    retried = module.evaluate_due_outcomes(evaluation.id)
    visible = module.query_outcomes(OutcomeFilters()).items

    assert retried.id != first_run.id
    assert [item.status for item in visible] == [
        "COMPLETED",
        "COMPLETED",
        "PENDING",
    ]
    assert visible[-1].expected_evaluation_trade_date == date(2026, 9, 3)


def test_evaluation_and_queries_are_isolated_to_active_batch_source(session_factory):
    trading_dates = [
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    with session_factory() as session:
        demo_batch = add_batch(session, date(2026, 8, 27), source="demo-v1")
        real_batch = add_batch(
            session,
            date(2026, 8, 27),
            source="tencent-sina-v1",
        )
        evaluation = add_batch(
            session,
            trading_dates[-1],
            active=True,
            source="tencent-sina-v1",
        )
        demo_candidate = add_candidate(session, demo_batch, "SH", "600001")
        real_candidate = add_candidate(session, real_batch, "SH", "600002")
        seed_calendar(session)
        session.add(
            CandidateOutcome(
                candidate_result_id=demo_candidate.id,
                source_batch_id=demo_batch.id,
                evaluation_batch_id=demo_batch.id,
                source_trade_date=demo_batch.trade_date,
                rule_version=demo_batch.rule_version,
                horizon_trading_days=1,
                status="COMPLETED",
                calculation_version="outcome-v1",
            )
        )
        for code in (demo_candidate.stock_code, real_candidate.stock_code):
            for trade_date in trading_dates:
                add_price(session, evaluation.id, "SH", code, trade_date)
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    run = module.evaluate_due_outcomes(evaluation.id)
    page = module.query_outcomes(OutcomeFilters())
    summary = module.summarize_outcomes(OutcomeFilters())

    assert (run.expected_count, run.completed_count, run.pending_count) == (3, 3, 0)
    assert {item.candidate_result_id for item in page.items} == {real_candidate.id}
    assert summary.total == page.total == 3
    assert summary.max_drawdown_approx == pytest.approx(-10)
    with session_factory() as session:
        demo_rows = session.scalars(
            select(CandidateOutcome).where(
                CandidateOutcome.candidate_result_id == demo_candidate.id
            )
        ).all()
        real_rows = session.scalars(
            select(CandidateOutcome).where(
                CandidateOutcome.candidate_result_id == real_candidate.id
            )
        ).all()
        assert len(demo_rows) == 1
        assert len(real_rows) == 3


def test_completed_legacy_unowned_rows_trigger_clean_attempt_and_stay_hidden(
    session_factory,
):
    trading_dates = [
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    with session_factory() as session:
        demo_batch = add_batch(session, date(2026, 8, 27), source="demo-v1")
        real_batch = add_batch(
            session,
            date(2026, 8, 27),
            source="tencent-sina-v1",
        )
        evaluation = add_batch(
            session,
            trading_dates[-1],
            active=True,
            source="tencent-sina-v1",
        )
        demo_candidate = add_candidate(session, demo_batch, "SH", "600001")
        real_candidate = add_candidate(session, real_batch, "SH", "600002")
        seed_calendar(session)
        for candidate, source_batch in (
            (demo_candidate, demo_batch),
            (real_candidate, real_batch),
        ):
            for horizon in (1, 3, 5):
                session.add(
                    CandidateOutcome(
                        candidate_result_id=candidate.id,
                        source_batch_id=source_batch.id,
                        evaluation_batch_id=evaluation.id,
                        source_trade_date=source_batch.trade_date,
                        rule_version=source_batch.rule_version,
                        horizon_trading_days=horizon,
                        reference_trade_date=trading_dates[0],
                        evaluation_trade_date=trading_dates[horizon - 1],
                        reference_price=10,
                        evaluation_price=11,
                        return_rate=10,
                        mfe=10,
                        mae=-5,
                        status="COMPLETED",
                        calculation_version="outcome-v1",
                    )
                )
        session.add(
            OutcomeRun(
                evaluation_batch_id=evaluation.id,
                calculation_version="outcome-v1",
                rule_version=evaluation.rule_version,
                status="COMPLETED",
                expected_count=3,
                completed_count=3,
                pending_count=0,
                unavailable_count=0,
            )
        )
        for trade_date in trading_dates:
            add_price(
                session,
                evaluation.id,
                real_candidate.market,
                real_candidate.stock_code,
                trade_date,
            )
        session.commit()

    repaired = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(
        evaluation.id
    )

    assert (repaired.expected_count, repaired.completed_count) == (3, 3)
    with session_factory() as session:
        demo_rows = session.scalars(
            select(CandidateOutcome)
            .where(CandidateOutcome.candidate_result_id == demo_candidate.id)
            .order_by(CandidateOutcome.horizon_trading_days)
        ).all()
        assert len(demo_rows) == 3
        assert all(item.outcome_run_id is None for item in demo_rows)
    assert CandidateOutcomeModule(session_factory).query_outcomes(
        OutcomeFilters()
    ).total == 3


def test_legacy_cross_source_pollution_without_run_is_never_published(tmp_path):
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'pollution-cleanup.db'}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        demo = add_batch(session, date(2026, 8, 27), source="demo-v1")
        evaluation = add_batch(
            session,
            date(2026, 9, 3),
            source="tencent-sina-v1",
            active=True,
        )
        candidates = [
            add_candidate(session, demo, "SH", f"60000{index}")
            for index in range(3)
        ]
        session.add_all(
            [
                CandidateOutcome(
                    candidate_result_id=candidate.id,
                    source_batch_id=demo.id,
                    evaluation_batch_id=evaluation.id,
                    source_trade_date=demo.trade_date,
                    rule_version=demo.rule_version,
                    horizon_trading_days=1,
                    reference_trade_date=date(2026, 8, 28),
                    evaluation_trade_date=date(2026, 8, 28),
                    reference_price=10,
                    evaluation_price=11,
                    return_rate=10,
                    mfe=10,
                    mae=-5,
                    status="COMPLETED",
                    calculation_version="outcome-v1",
                )
                for candidate in candidates
            ]
        )
        session.commit()

    module = CandidateOutcomeModule(factory)
    completed = module.evaluate_due_outcomes(evaluation.id)
    assert completed.expected_count == 0
    assert module.query_outcomes(OutcomeFilters()).total == 0
    with factory() as session:
        rows = session.scalars(
            select(CandidateOutcome).order_by(CandidateOutcome.id)
        ).all()
        assert len(rows) == 3
        assert all(item.outcome_run_id is None for item in rows)
    factory.kw["bind"].dispose()


def test_uses_exact_evaluation_batch_raw_market_and_target_dates(session_factory):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        other = add_batch(session, date(2026, 9, 1))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        sh = add_candidate(session, source, "SH", "600000")
        add_candidate(session, source, "SZ", "600000")
        session.add_all(
            [
                StockBasic(market="SH", stock_code="600000", stock_name="浦发银行"),
                StockBasic(market="SZ", stock_code="600000", stock_name="深市同码"),
            ]
        )
        for trade_date in dates:
            add_price(session, evaluation.id, "SH", "600000", trade_date)
            add_price(session, evaluation.id, "SH", "600000", trade_date, adjustment="qfq")
            add_price(session, other.id, "SH", "600000", trade_date)
            add_price(session, evaluation.id, "SZ", "600000", trade_date)
        session.flush()
        missing = session.scalar(
            select(DailyPrice).where(
                DailyPrice.batch_id == evaluation.id,
                DailyPrice.market == "SH",
                DailyPrice.trade_date == dates[2],
                DailyPrice.adjustment == "raw",
            )
        )
        session.delete(missing)
        session.commit()

    CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    module = CandidateOutcomeModule(session_factory)
    outcomes = module.get_candidate_outcomes(sh.id)

    assert outcomes[0].status == "COMPLETED"
    assert outcomes[1].status == "UNAVAILABLE"
    assert outcomes[1].unavailable_reason == "PRICE_DATA_MISSING"
    assert outcomes[1].reference_trade_date == dates[0]
    assert outcomes[1].evaluation_trade_date == dates[2]
    names_by_market = {
        item.market: item.stock_name
        for item in module.query_outcomes(OutcomeFilters(horizon=1)).items
    }
    assert names_by_market == {"SH": "浦发银行", "SZ": "深市同码"}


def test_maps_reference_evaluation_and_intermediate_suspensions(session_factory):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 1), active=True)
        reference_suspended = add_candidate(session, source, "SH", "600001")
        evaluation_suspended = add_candidate(session, source, "SH", "600002")
        intermediate_suspended = add_candidate(session, source, "SH", "600003")
        for candidate in (
            reference_suspended,
            evaluation_suspended,
            intermediate_suspended,
        ):
            for index, trade_date in enumerate(dates[:3]):
                suspended = (
                    candidate.id == reference_suspended.id
                    and index == 0
                    or candidate.id == evaluation_suspended.id
                    and index == 2
                    or candidate.id == intermediate_suspended.id
                    and index == 1
                )
                add_price(
                    session,
                    evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                    suspended=suspended,
                    volume=0 if suspended else 100,
                )
        session.commit()

    CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    module = CandidateOutcomeModule(session_factory)

    assert module.get_candidate_outcomes(reference_suspended.id)[0].unavailable_reason == (
        "REFERENCE_UNAVAILABLE"
    )
    assert module.get_candidate_outcomes(evaluation_suspended.id)[1].unavailable_reason == (
        "EVALUATION_UNAVAILABLE"
    )
    assert module.get_candidate_outcomes(intermediate_suspended.id)[1].status == "COMPLETED"


def test_run_is_idempotent_and_calculation_versions_are_isolated(session_factory):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for trade_date in dates:
            add_price(session, evaluation.id, "SH", "600000", trade_date)
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    first = module.evaluate_due_outcomes(evaluation.id)
    second = module.evaluate_due_outcomes(evaluation.id)
    CandidateOutcomeModule(session_factory, calculation_version="outcome-v2").evaluate_due_outcomes(
        evaluation.id
    )

    assert first == second
    with session_factory() as session:
        assert len(session.scalars(select(OutcomeRun)).all()) == 2
        rows = session.scalars(
            select(CandidateOutcome).where(CandidateOutcome.candidate_result_id == candidate.id)
        ).all()
        assert len(rows) == 6


def test_completed_run_review_is_read_only_and_pages_terminal_outcomes(
    tmp_path,
    monkeypatch,
):
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'outcome-review.db'}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidates = [
            add_candidate(session, source, "SH", f"60000{index}")
            for index in range(3)
        ]
        for candidate in candidates:
            for trade_date in dates:
                add_price(
                    session,
                    evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                )
        session.commit()

    module = CandidateOutcomeModule(factory)
    first = module.evaluate_due_outcomes(evaluation.id)
    monkeypatch.setattr(outcome_application, "_REVIEW_CHUNK_SIZE", 2)
    original_authoritative_review = module._has_invalid_authoritative_outcomes
    writer_committed: list[bool] = []
    review_queries: list[str] = []

    def capture_review_sql(_conn, _cursor, statement, _params, _context, _many):
        normalized = statement.lower()
        if "order by candidate_outcome.id" in normalized and " limit " in normalized:
            review_queries.append(normalized)

    def write_during_review(
        session, evaluation_batch, outcome_run_id, rule_version
    ):
        with factory.begin() as writer:
            writer.add(
                StockBasic(
                    market="SH",
                    stock_code="699999",
                    stock_name="复核并行写入",
                )
            )
        writer_committed.append(True)
        return original_authoritative_review(
            session,
            evaluation_batch,
            outcome_run_id,
            rule_version,
        )

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_review_sql)
    monkeypatch.setattr(
        module,
        "_has_invalid_authoritative_outcomes",
        write_during_review,
    )
    try:
        repeated = module.evaluate_due_outcomes(evaluation.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture_review_sql)

    assert repeated.id == first.id
    assert writer_committed == [True]
    assert len(review_queries) >= 5
    factory.kw["bind"].dispose()


@pytest.mark.parametrize("calculation_version", [None, "", "   ", "x" * 33, 123])
def test_calculation_version_must_be_a_nonblank_string_within_storage_limit(
    session_factory, calculation_version
):
    with pytest.raises(ValueError, match="calculation_version"):
        CandidateOutcomeModule(
            session_factory,
            calculation_version=calculation_version,
        )


@pytest.mark.parametrize("calculation_version", ["x" * 32, " outcome-v2 "])
def test_valid_calculation_version_is_preserved_without_rewriting(
    session_factory, calculation_version
):
    module = CandidateOutcomeModule(
        session_factory,
        calculation_version=calculation_version,
    )

    assert module.calculation_version == calculation_version


def test_non_sqlite_evaluation_fails_explicitly_before_database_access():
    class NonSqliteDialect:
        name = "postgresql"

    class NonSqliteBind:
        dialect = NonSqliteDialect()

    class UncallableFactory:
        kw = {"bind": NonSqliteBind()}

        def __call__(self):
            raise AssertionError("non-SQLite evaluation must fail before database access")

    module = CandidateOutcomeModule(UncallableFactory())  # type: ignore[arg-type]

    with pytest.raises(
        UnsupportedOutcomeEvaluationBackendError,
        match="cross-process serialization",
    ):
        module.evaluate_due_outcomes(1)


def test_pending_run_takes_execution_ownership_and_completes(session_factory):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for trade_date in dates:
            add_price(session, evaluation.id, "SH", "600000", trade_date)
        pending_run = OutcomeRun(
            evaluation_batch_id=evaluation.id,
            calculation_version="outcome-v1",
            rule_version=evaluation.rule_version,
            status="PENDING",
        )
        session.add(pending_run)
        session.commit()

    result = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)

    assert result.id != pending_run.id
    assert result.status == "COMPLETED"
    assert (result.expected_count, result.completed_count) == (3, 3)
    assert len(CandidateOutcomeModule(session_factory).get_candidate_outcomes(candidate.id)) == 3


def test_unknown_run_state_is_rejected_without_silent_execution(session_factory):
    with session_factory() as session:
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        run = OutcomeRun(
            evaluation_batch_id=evaluation.id,
            calculation_version="outcome-v1",
            rule_version=evaluation.rule_version,
            status="PAUSED",
        )
        session.add(run)
        session.commit()

    with pytest.raises(OutcomeRunStateError) as caught:
        CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)

    assert caught.value.run_id == run.id
    assert caught.value.status == "PAUSED"
    with session_factory() as session:
        persisted = session.get(OutcomeRun, run.id)
        assert persisted.status == "PAUSED"


def test_file_sqlite_serializes_same_run_and_returns_idempotently(
    tmp_path, monkeypatch
):
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'outcome-race.db'}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        add_candidate(session, source, "SH", "600000")
        for trade_date in dates:
            add_price(session, evaluation.id, "SH", "600000", trade_date)
        session.commit()

    owner = CandidateOutcomeModule(factory)
    contender = CandidateOutcomeModule(factory)
    original_evaluate = owner._evaluate_candidate_chunk
    owner_entered = Event()
    allow_owner_to_finish = Event()
    contender_call_started = Event()

    def pause_before_writes(
        session, evaluation_batch_id, after_candidate_id, **kwargs
    ):
        owner_entered.set()
        assert allow_owner_to_finish.wait(timeout=5)
        return original_evaluate(
            session,
            evaluation_batch_id,
            after_candidate_id,
            **kwargs,
        )

    def run_contender():
        contender_call_started.set()
        return contender.evaluate_due_outcomes(evaluation.id)

    monkeypatch.setattr(owner, "_evaluate_candidate_chunk", pause_before_writes)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner_future = pool.submit(owner.evaluate_due_outcomes, evaluation.id)
        assert owner_entered.wait(timeout=5)
        contender_future = pool.submit(run_contender)
        assert contender_call_started.wait(timeout=5)
        assert not contender_future.done()
        allow_owner_to_finish.set()
        completed = owner_future.result(timeout=5)
        idempotent = contender_future.result(timeout=5)

    with factory() as session:
        runs = session.scalars(select(OutcomeRun)).all()
        assert len(runs) == 1
        assert runs[0].status == "COMPLETED"
        assert runs[0].error_summary is None
    assert completed.status == "COMPLETED"
    assert idempotent.id == completed.id
    assert idempotent.status == "COMPLETED"
    factory.kw["bind"].dispose()


def test_file_sqlite_serializes_different_evaluation_batches(tmp_path, monkeypatch):
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'outcome-global-lock.db'}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        first_evaluation = add_batch(session, date(2026, 9, 2))
        second_evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for trade_date in dates[:4]:
            add_price(
                session,
                first_evaluation.id,
                "SH",
                "600000",
                trade_date,
            )
        for trade_date in dates:
            add_price(
                session,
                second_evaluation.id,
                "SH",
                "600000",
                trade_date,
            )
        session.commit()

    first = CandidateOutcomeModule(factory)
    second = CandidateOutcomeModule(factory)
    original_first_evaluate = first._evaluate_candidate_chunk
    original_second_evaluate = second._evaluate_candidate_chunk
    first_entered = Event()
    allow_first_to_finish = Event()
    second_call_started = Event()
    second_entered = Event()

    def pause_first(session, evaluation_batch_id, after_candidate_id, **kwargs):
        first_entered.set()
        assert allow_first_to_finish.wait(timeout=5)
        return original_first_evaluate(
            session,
            evaluation_batch_id,
            after_candidate_id,
            **kwargs,
        )

    def mark_second(session, evaluation_batch_id, after_candidate_id, **kwargs):
        second_entered.set()
        return original_second_evaluate(
            session,
            evaluation_batch_id,
            after_candidate_id,
            **kwargs,
        )

    def run_second():
        second_call_started.set()
        return second.evaluate_due_outcomes(second_evaluation.id)

    monkeypatch.setattr(first, "_evaluate_candidate_chunk", pause_first)
    monkeypatch.setattr(second, "_evaluate_candidate_chunk", mark_second)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            first.evaluate_due_outcomes, first_evaluation.id
        )
        assert first_entered.wait(timeout=5)
        second_future = pool.submit(run_second)
        assert second_call_started.wait(timeout=5)
        assert not second_entered.wait(timeout=0.5)
        assert not second_future.done()
        allow_first_to_finish.set()
        first_result = first_future.result(timeout=5)
        second_result = second_future.result(timeout=5)

    with factory() as session:
        runs = session.scalars(select(OutcomeRun).order_by(OutcomeRun.id)).all()
        outcomes = session.scalars(
            select(CandidateOutcome).order_by(CandidateOutcome.horizon_trading_days)
        ).all()
        assert [run.status for run in runs] == ["COMPLETED", "COMPLETED"]
        assert all(run.error_summary is None for run in runs)
        assert len(outcomes) == 6
        assert [outcome.status for outcome in outcomes].count("COMPLETED") == 5
        assert [outcome.status for outcome in outcomes].count("PENDING") == 1
        assert {outcome.evaluation_batch_id for outcome in outcomes} == {
            first_evaluation.id,
            second_evaluation.id,
        }
        assert {outcome.candidate_result_id for outcome in outcomes} == {candidate.id}
    assert first_result.status == second_result.status == "COMPLETED"
    factory.kw["bind"].dispose()


def test_file_sqlite_serializes_evaluations_across_processes(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'outcome-process-lock.db'}"
    factory = create_sqlite_session_factory(database_url)
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        first_evaluation = add_batch(session, date(2026, 9, 2))
        second_evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for trade_date in dates[:4]:
            add_price(
                session,
                first_evaluation.id,
                "SH",
                "600000",
                trade_date,
            )
        for trade_date in dates:
            add_price(
                session,
                second_evaluation.id,
                "SH",
                "600000",
                trade_date,
            )
        session.commit()

    context = get_context("spawn")
    first_call_started = context.Event()
    first_entered = context.Event()
    release_first = context.Event()
    second_call_started = context.Event()
    second_entered = context.Event()
    results = context.Queue()
    first_process = context.Process(
        target=evaluate_in_separate_process,
        args=(
            database_url,
            first_evaluation.id,
            first_call_started,
            first_entered,
            release_first,
            results,
        ),
    )
    second_process = context.Process(
        target=evaluate_in_separate_process,
        args=(
            database_url,
            second_evaluation.id,
            second_call_started,
            second_entered,
            None,
            results,
        ),
    )

    first_process.start()
    assert first_call_started.wait(timeout=10)
    assert first_entered.wait(timeout=10)
    second_process.start()
    try:
        assert second_call_started.wait(timeout=10)
        assert not second_entered.wait(timeout=0.5)
        with pytest.raises(Empty):
            results.get(timeout=0.1)
    finally:
        release_first.set()
        first_process.join(timeout=10)
        second_process.join(timeout=10)
        if first_process.is_alive():
            first_process.terminate()
        if second_process.is_alive():
            second_process.terminate()

    assert first_process.exitcode == second_process.exitcode == 0
    assert (tmp_path / "outcome-process-lock.db.candidate-outcomes.lock").is_file()
    assert sorted([results.get(timeout=2), results.get(timeout=2)]) == [
        ("ok", "COMPLETED"),
        ("ok", "COMPLETED"),
    ]
    with factory() as session:
        runs = session.scalars(select(OutcomeRun).order_by(OutcomeRun.id)).all()
        outcomes = session.scalars(
            select(CandidateOutcome).order_by(CandidateOutcome.horizon_trading_days)
        ).all()
        assert [run.status for run in runs] == ["COMPLETED", "COMPLETED"]
        assert all(run.error_summary is None for run in runs)
        assert len(outcomes) == 6
        assert [outcome.status for outcome in outcomes].count("COMPLETED") == 5
        assert [outcome.status for outcome in outcomes].count("PENDING") == 1
        assert {outcome.evaluation_batch_id for outcome in outcomes} == {
            first_evaluation.id,
            second_evaluation.id,
        }
        assert {outcome.candidate_result_id for outcome in outcomes} == {candidate.id}
    factory.kw["bind"].dispose()


def test_failed_run_is_persisted_without_fake_counters_and_can_retry(
    session_factory, monkeypatch, caplog
):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        add_candidate(session, source, "SH", "600000")
        add_candidate(session, source, "SZ", "000001")
        for market, code in (("SH", "600000"), ("SZ", "000001")):
            for trade_date in dates:
                add_price(session, evaluation.id, market, code, trade_date)
        session.commit()

    original = outcome_application.calculate_outcome
    calls = 0

    sensitive_message = (
        "private-note sqlite:////Users/private/outcomes.db "
        "SELECT * FROM candidate_outcome"
    )

    def fail_after_first_candidate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError(sensitive_message)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        outcome_application, "calculate_outcome", fail_after_first_candidate
    )
    module = CandidateOutcomeModule(session_factory)
    with (
        caplog.at_level("ERROR", logger="app.application.candidate_outcomes"),
        pytest.raises(RuntimeError, match="private-note"),
    ):
        module.evaluate_due_outcomes(evaluation.id)
    assert calls == 4

    with session_factory() as session:
        run = session.scalar(select(OutcomeRun))
        assert run.status == "FAILED"
        assert run.expected_count == 0
        assert run.completed_count == 0
        assert run.finished_at is not None
        assert run.error_summary == "候选评价失败，可重试"
        assert session.scalar(select(CandidateOutcome)) is None
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert f"batch_id={evaluation.id}" in logs
    assert "error_type=RuntimeError" in logs
    assert "private-note" not in logs
    assert "SELECT" not in logs
    assert "/Users/private" not in logs

    monkeypatch.setattr(outcome_application, "calculate_outcome", original)
    retried = module.evaluate_due_outcomes(evaluation.id)
    assert retried.status == "COMPLETED"
    assert retried.expected_count == 6


def test_candidate_chunks_commit_independently_and_failed_run_retries_idempotently(
    tmp_path,
    monkeypatch,
):
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'outcome-chunks.db'}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 1), active=True)
        candidates = [
            add_candidate(session, source, "SH", "600000"),
            add_candidate(session, source, "SZ", "000001"),
        ]
        for candidate in candidates:
            for trade_date in dates[:3]:
                add_price(
                    session,
                    evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                )
        session.commit()

    monkeypatch.setattr(outcome_application, "_CANDIDATE_CHUNK_SIZE", 1)
    module = CandidateOutcomeModule(factory)
    original_chunk = module._evaluate_candidate_chunk
    chunk_calls = 0

    def fail_second_chunk(
        session, evaluation_batch_id, after_candidate_id, **kwargs
    ):
        nonlocal chunk_calls
        chunk_calls += 1
        if chunk_calls == 2:
            with factory() as observer:
                assert len(observer.scalars(select(CandidateOutcome)).all()) == 3
            raise RuntimeError("second chunk failed")
        return original_chunk(
            session, evaluation_batch_id, after_candidate_id, **kwargs
        )

    monkeypatch.setattr(module, "_evaluate_candidate_chunk", fail_second_chunk)
    with pytest.raises(RuntimeError, match="second chunk failed"):
        module.evaluate_due_outcomes(evaluation.id)

    with factory() as session:
        run = session.scalar(select(OutcomeRun))
        persisted = session.scalars(
            select(CandidateOutcome).order_by(
                CandidateOutcome.candidate_result_id,
                CandidateOutcome.horizon_trading_days,
            )
        ).all()
        assert run.status == "FAILED"
        assert len(persisted) == 3
        assert {item.candidate_result_id for item in persisted} == {candidates[0].id}
        assert [item.status for item in persisted] == [
            "COMPLETED",
            "COMPLETED",
            "PENDING",
        ]
        assert {item.evaluation_batch_id for item in persisted} == {evaluation.id}

    hidden_page = module.query_outcomes(OutcomeFilters())
    hidden_summary = module.summarize_outcomes(OutcomeFilters())
    assert hidden_page.total == hidden_summary.total == 0
    assert module.get_candidate_outcomes(candidates[0].id) == []
    assert module.get_batch_statuses(source.id) == {
        candidates[0].id: "PENDING",
        candidates[1].id: "PENDING",
    }

    monkeypatch.setattr(module, "_evaluate_candidate_chunk", original_chunk)
    retried = module.evaluate_due_outcomes(evaluation.id)
    assert (
        retried.status,
        retried.expected_count,
        retried.completed_count,
        retried.pending_count,
    ) == (
        "COMPLETED",
        6,
        4,
        2,
    )
    assert module.query_outcomes(OutcomeFilters()).total == 6
    summary = module.summarize_outcomes(OutcomeFilters())
    assert (summary.total, summary.completed, summary.pending) == (6, 4, 2)
    assert len(module.get_candidate_outcomes(candidates[0].id)) == 3
    assert module.get_batch_statuses(source.id) == {
        candidates[0].id: "PARTIAL",
        candidates[1].id: "PARTIAL",
    }
    with factory() as session:
        assert len(session.scalars(select(CandidateOutcome)).all()) == 9
    factory.kw["bind"].dispose()


def test_failed_new_snapshot_keeps_previous_completed_snapshot_visible(
    tmp_path,
    monkeypatch,
):
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'outcome-snapshots.db'}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        first_evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidates = [
            add_candidate(session, source, "SH", "600000"),
            add_candidate(session, source, "SZ", "000001"),
        ]
        for candidate in candidates:
            for trade_date in dates:
                add_price(
                    session,
                    first_evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                )
        session.commit()

    module = CandidateOutcomeModule(factory)
    first_run = module.evaluate_due_outcomes(first_evaluation.id)
    first_page = module.query_outcomes(OutcomeFilters())
    assert first_run.completed_count == first_page.total == 6
    assert {item.reference_price for item in first_page.items} == {10}

    with factory() as session:
        first_evaluation = session.get(DataBatch, first_evaluation.id)
        first_evaluation.is_active = False
        second_evaluation = add_batch(session, date(2026, 9, 4), active=True)
        session.add(
            TradeCalendar(market="CN", trade_date=date(2026, 9, 4), is_open=True)
        )
        for candidate in candidates:
            for trade_date in [*dates, date(2026, 9, 4)]:
                add_price(
                    session,
                    second_evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                    open_price=20,
                    high=22,
                    low=19,
                    close=21,
                )
        session.commit()

    monkeypatch.setattr(outcome_application, "_CANDIDATE_CHUNK_SIZE", 1)
    original_chunk = module._evaluate_candidate_chunk
    chunk_calls = 0

    def fail_second_chunk(
        session, evaluation_batch_id, after_candidate_id, **kwargs
    ):
        nonlocal chunk_calls
        chunk_calls += 1
        if chunk_calls == 2:
            raise RuntimeError("second snapshot chunk failed")
        return original_chunk(
            session, evaluation_batch_id, after_candidate_id, **kwargs
        )

    monkeypatch.setattr(module, "_evaluate_candidate_chunk", fail_second_chunk)
    with pytest.raises(RuntimeError, match="second snapshot chunk failed"):
        module.evaluate_due_outcomes(second_evaluation.id)

    visible_after_failure = module.query_outcomes(OutcomeFilters())
    assert visible_after_failure.total == 6
    assert {item.evaluation_batch_id for item in visible_after_failure.items} == {
        first_evaluation.id
    }
    assert {item.reference_price for item in visible_after_failure.items} == {10}
    assert module.summarize_outcomes(OutcomeFilters()).completed == 6
    assert len(module.get_candidate_outcomes(candidates[0].id)) == 3
    assert module.get_batch_statuses(source.id) == {
        candidates[0].id: "COMPLETED",
        candidates[1].id: "COMPLETED",
    }

    monkeypatch.setattr(module, "_evaluate_candidate_chunk", original_chunk)
    retried = module.evaluate_due_outcomes(second_evaluation.id)

    assert (retried.expected_count, retried.completed_count) == (6, 6)
    visible_after_publish = module.query_outcomes(OutcomeFilters())
    assert visible_after_publish.total == 6
    assert {item.evaluation_batch_id for item in visible_after_publish.items} == {
        second_evaluation.id
    }
    assert {item.reference_price for item in visible_after_publish.items} == {20}
    with factory() as session:
        assert session.scalar(select(func.count(CandidateOutcome.id))) == 15
        assert session.scalar(select(func.count(OutcomeRun.id))) == 3
    factory.kw["bind"].dispose()


def test_failed_rebuild_attempt_keeps_completed_attempt_published(
    tmp_path,
    monkeypatch,
):
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'outcome-attempts.db'}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidates = [
            add_candidate(session, source, "SH", "600000"),
            add_candidate(session, source, "SZ", "000001"),
        ]
        for candidate in candidates:
            for trade_date in dates:
                add_price(
                    session,
                    evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                )
        session.commit()

    module = CandidateOutcomeModule(factory)
    first_run = module.evaluate_due_outcomes(evaluation.id)
    with factory() as session:
        polluted_batch = add_batch(
            session,
            date(2026, 8, 27),
            source="demo-v1",
        )
        polluted_candidate = add_candidate(session, polluted_batch, "SH", "600999")
        session.add(
            CandidateOutcome(
                candidate_result_id=polluted_candidate.id,
                source_batch_id=polluted_batch.id,
                evaluation_batch_id=evaluation.id,
                outcome_run_id=first_run.id,
                source_trade_date=polluted_batch.trade_date,
                rule_version=polluted_batch.rule_version,
                horizon_trading_days=1,
                status="PENDING",
                calculation_version="outcome-v1",
            )
        )
        session.commit()

    monkeypatch.setattr(outcome_application, "_CANDIDATE_CHUNK_SIZE", 1)
    original_chunk = module._evaluate_candidate_chunk
    chunk_calls = 0

    def fail_second_chunk(*args, **kwargs):
        nonlocal chunk_calls
        chunk_calls += 1
        if chunk_calls == 2:
            raise RuntimeError("rebuild attempt failed")
        return original_chunk(*args, **kwargs)

    monkeypatch.setattr(module, "_evaluate_candidate_chunk", fail_second_chunk)
    with pytest.raises(RuntimeError, match="rebuild attempt failed"):
        module.evaluate_due_outcomes(evaluation.id)

    visible = module.query_outcomes(OutcomeFilters())
    assert visible.total == 6
    assert {item.outcome_run_id for item in visible.items} == {first_run.id}
    with factory() as session:
        runs = session.scalars(select(OutcomeRun).order_by(OutcomeRun.attempt_no)).all()
        assert [run.status for run in runs] == ["COMPLETED", "FAILED"]
        assert [run.attempt_no for run in runs] == [1, 2]


def test_new_evaluation_advances_pending_cohorts_from_all_rule_versions(
    session_factory,
):
    with session_factory() as session:
        dates = seed_calendar(session)
        v1_source = add_batch(
            session,
            date(2026, 8, 27),
            rule_version="rules-v1",
        )
        v1_evaluation = add_batch(
            session,
            date(2026, 9, 1),
            rule_version="rules-v1",
        )
        v2_source = add_batch(
            session,
            date(2026, 8, 28),
            rule_version="rules-v2",
        )
        v2_evaluation = add_batch(
            session,
            date(2026, 9, 3),
            rule_version="rules-v2",
            active=True,
        )
        v1_candidate = add_candidate(session, v1_source, "SH", "600001")
        v2_candidate = add_candidate(session, v2_source, "SZ", "000001")
        for trade_date in dates[:3]:
            add_price(
                session,
                v1_evaluation.id,
                v1_candidate.market,
                v1_candidate.stock_code,
                trade_date,
            )
        for candidate in (v1_candidate, v2_candidate):
            for trade_date in dates:
                add_price(
                    session,
                    v2_evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    module.evaluate_due_outcomes(v1_evaluation.id)
    module.evaluate_due_outcomes(v2_evaluation.id)

    v1_page = module.query_outcomes(
        OutcomeFilters(rule_version="rules-v1", horizon=5)
    )
    assert v1_page.total == 1
    assert v1_page.items[0].status == "COMPLETED"
    with session_factory() as session:
        second_batch_runs = session.scalars(
            select(OutcomeRun).where(
                OutcomeRun.evaluation_batch_id == v2_evaluation.id
            )
        ).all()
        assert {run.rule_version for run in second_batch_runs} == {
            "rules-v1",
            "rules-v2",
        }


def test_secondary_rule_failure_is_isolated_and_retryable(
    session_factory,
    monkeypatch,
):
    with session_factory() as session:
        dates = seed_calendar(session)
        v1_source = add_batch(
            session,
            date(2026, 8, 27),
            rule_version="rules-v1",
        )
        v2_source = add_batch(
            session,
            date(2026, 8, 28),
            rule_version="rules-v2",
        )
        evaluation = add_batch(
            session,
            date(2026, 9, 3),
            rule_version="rules-v2",
            active=True,
        )
        candidates = (
            add_candidate(session, v1_source, "SH", "600001"),
            add_candidate(session, v2_source, "SZ", "000001"),
        )
        for candidate in candidates:
            for trade_date in dates:
                add_price(
                    session,
                    evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    original_chunk = module._evaluate_candidate_chunk
    failed_once = False

    def fail_v1_once(*args, **kwargs):
        nonlocal failed_once
        if kwargs["rule_version"] == "rules-v1" and not failed_once:
            failed_once = True
            raise RuntimeError("secondary cohort failed")
        return original_chunk(*args, **kwargs)

    monkeypatch.setattr(module, "_evaluate_candidate_chunk", fail_v1_once)
    primary_run = module.evaluate_due_outcomes(evaluation.id)

    assert primary_run.status == "COMPLETED"
    assert module.query_outcomes(
        OutcomeFilters(rule_version="rules-v1")
    ).total == 0

    monkeypatch.setattr(module, "_evaluate_candidate_chunk", original_chunk)
    retried_primary_run = module.evaluate_due_outcomes(evaluation.id)

    assert retried_primary_run.id == primary_run.id
    assert module.query_outcomes(
        OutcomeFilters(rule_version="rules-v1")
    ).total == 3
    with session_factory() as session:
        runs = session.scalars(
            select(OutcomeRun)
            .where(OutcomeRun.evaluation_batch_id == evaluation.id)
            .order_by(OutcomeRun.rule_version, OutcomeRun.attempt_no)
        ).all()
        assert [
            (run.rule_version, run.attempt_no, run.status) for run in runs
        ] == [
            ("rules-v1", 1, "FAILED"),
            ("rules-v1", 2, "COMPLETED"),
            ("rules-v2", 1, "COMPLETED"),
        ]


def test_legacy_unbound_pending_is_hidden_and_rebound_by_retry(session_factory):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 1), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for trade_date in dates[:3]:
            add_price(
                session,
                evaluation.id,
                candidate.market,
                candidate.stock_code,
                trade_date,
            )
        session.add(
            CandidateOutcome(
                candidate_result_id=candidate.id,
                source_batch_id=source.id,
                evaluation_batch_id=None,
                source_trade_date=source.trade_date,
                rule_version=source.rule_version,
                horizon_trading_days=5,
                status="PENDING",
                calculation_version="outcome-v1",
            )
        )
        session.add(
            OutcomeRun(
                evaluation_batch_id=evaluation.id,
                calculation_version="outcome-v1",
                rule_version=evaluation.rule_version,
                status="COMPLETED",
                expected_count=1,
                completed_count=0,
                unavailable_count=0,
                pending_count=1,
            )
        )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    assert module.query_outcomes(OutcomeFilters()).total == 0
    assert module.summarize_outcomes(OutcomeFilters()).total == 0
    assert module.get_candidate_outcomes(candidate.id) == []

    repaired = module.evaluate_due_outcomes(evaluation.id)

    assert (
        repaired.expected_count,
        repaired.completed_count,
        repaired.pending_count,
    ) == (3, 2, 1)
    assert module.query_outcomes(OutcomeFilters()).total == 3
    with session_factory() as session:
        outcomes = session.scalars(
            select(CandidateOutcome).where(
                CandidateOutcome.candidate_result_id == candidate.id
            )
        ).all()
        assert len(outcomes) == 4
        assert {outcome.evaluation_batch_id for outcome in outcomes} == {
            None,
            evaluation.id,
        }


def test_recovery_skips_running_process_and_recovers_after_file_lock_release(
    tmp_path,
):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'outcome-recovery-lock.db'}"
    factory = create_sqlite_session_factory(database_url)
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        run = OutcomeRun(
            evaluation_batch_id=evaluation.id,
            calculation_version="outcome-v1",
            rule_version=evaluation.rule_version,
            status="RUNNING",
        )
        session.add(run)
        session.commit()
        run_id = run.id

    context = get_context("spawn")
    lock_acquired = context.Event()
    release_lock = context.Event()
    holder = context.Process(
        target=hold_outcome_lock_in_separate_process,
        args=(database_url, lock_acquired, release_lock),
    )
    holder.start()
    assert lock_acquired.wait(timeout=10)
    module = CandidateOutcomeModule(factory)
    try:
        assert module.recover_interrupted_runs() == 0
        with factory() as session:
            assert session.get(OutcomeRun, run_id).status == "RUNNING"
    finally:
        release_lock.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()

    assert holder.exitcode == 0
    assert module.recover_interrupted_runs() == 1
    with factory() as session:
        recovered = session.get(OutcomeRun, run_id)
        assert recovered.status == "FAILED"
        assert recovered.finished_at is not None
        assert recovered.error_summary == "应用进程中断，可重试"
    factory.kw["bind"].dispose()


def test_later_completed_snapshot_replaces_view_without_overwriting_history(
    session_factory,
):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        first_evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for trade_date in dates:
            add_price(session, first_evaluation.id, "SH", "600000", trade_date)
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    module.evaluate_due_outcomes(first_evaluation.id)
    original = module.get_candidate_outcomes(candidate.id)[0]

    with session_factory() as session:
        session.get(DataBatch, first_evaluation.id).is_active = False
        later = add_batch(session, date(2026, 9, 4), active=True)
        for trade_date in dates:
            add_price(
                session,
                later.id,
                "SH",
                "600000",
                trade_date,
                open_price=20,
                high=22,
                low=18,
                close=21,
            )
        session.commit()

    later_run = module.evaluate_due_outcomes(later.id)
    latest = module.get_candidate_outcomes(candidate.id)[0]
    assert (
        later_run.expected_count,
        later_run.completed_count,
        later_run.unavailable_count,
        later_run.pending_count,
    ) == (3, 3, 0, 0)
    assert latest.evaluation_batch_id == later.id
    assert latest.reference_price == 20
    assert original.evaluation_batch_id == first_evaluation.id
    with session_factory() as session:
        statuses = session.scalars(
            select(CandidateOutcome.status).where(
                CandidateOutcome.candidate_result_id == candidate.id,
                CandidateOutcome.calculation_version == "outcome-v1",
            )
        ).all()
    assert statuses.count("COMPLETED") == 6
    assert statuses.count("UNAVAILABLE") == later_run.unavailable_count
    assert statuses.count("PENDING") == later_run.pending_count


def test_queries_filters_pages_names_and_summarizes_completed_samples(session_factory):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        first = add_candidate(session, source, "SH", "600000")
        second = add_candidate(session, source, "SZ", "000001")
        session.add_all(
            [
                StockBasic(market="SH", stock_code="600000", stock_name="甲公司"),
                StockBasic(market="SZ", stock_code="000001", stock_name="乙公司"),
            ]
        )
        for candidate, close in ((first, 12), (second, 8)):
            for trade_date in dates:
                add_price(
                    session,
                    evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                    high=max(12, close),
                    low=min(8, close),
                    close=close,
                )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    module.evaluate_due_outcomes(evaluation.id)
    filters = OutcomeFilters(rule_version="rules-v1", horizon=1, status="COMPLETED")

    page = module.query_outcomes(filters)
    summary = module.summarize_outcomes(filters)

    assert page.total == 2
    assert [item.stock_name for item in page.items] == ["甲公司", "乙公司"]
    assert [item.candidate_result_id for item in page.items] == [first.id, second.id]
    assert summary.total == 2
    assert summary.completed == 2
    assert summary.mean_return_rate == pytest.approx(0)
    assert summary.median_return_rate == pytest.approx(0)
    assert summary.positive_return_ratio == pytest.approx(0.5)
    assert summary.mean_mfe == pytest.approx(20)
    assert summary.mean_mae == pytest.approx(-20)
    assert summary.max_drawdown_approx == pytest.approx(-20)
    assert CandidateOutcomeModule(session_factory).summarize_outcomes(
        OutcomeFilters(status="PENDING")
    ).mean_return_rate is None
    assert CandidateOutcomeModule(session_factory).summarize_outcomes(
        OutcomeFilters(status="PENDING")
    ).max_drawdown_approx is None

    second_page = module.query_outcomes(
        OutcomeFilters(horizon=1, status="COMPLETED", page=2, page_size=1)
    )
    assert second_page.total == 2
    assert [item.candidate_result_id for item in second_page.items] == [second.id]


def test_query_materializes_published_run_ids_before_count_and_items(
    tmp_path,
    monkeypatch,
):
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'query-published-snapshot.db'}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        source = add_batch(session, date(2026, 8, 27))
        first_evaluation = add_batch(session, date(2026, 9, 2), active=True)
        next_evaluation = add_batch(session, date(2026, 9, 3))
        first = add_candidate(session, source, "SH", "600001")
        second = add_candidate(session, source, "SZ", "000001")
        first_run = add_completed_run(session, first_evaluation)
        next_run = OutcomeRun(
            evaluation_batch_id=next_evaluation.id,
            calculation_version="outcome-v1",
            rule_version=source.rule_version,
            status="RUNNING",
        )
        session.add(next_run)
        session.flush()
        for run, candidate, return_rate in (
            (first_run, first, 1.0),
            (next_run, first, 2.0),
            (next_run, second, 3.0),
        ):
            session.add(
                CandidateOutcome(
                    candidate_result_id=candidate.id,
                    source_batch_id=source.id,
                    evaluation_batch_id=run.evaluation_batch_id,
                    outcome_run_id=run.id,
                    source_trade_date=source.trade_date,
                    rule_version=source.rule_version,
                    horizon_trading_days=1,
                    status="COMPLETED",
                    return_rate=return_rate,
                    calculation_version="outcome-v1",
                )
            )
        session.commit()

    module = CandidateOutcomeModule(factory)
    original_materialize = module._published_run_ids
    switched = False

    def publish_after_materialization(session, filters):
        nonlocal switched
        run_ids = original_materialize(session, filters)
        with factory.begin() as writer:
            writer.get(OutcomeRun, next_run.id).status = "COMPLETED"
        switched = True
        return run_ids

    monkeypatch.setattr(module, "_published_run_ids", publish_after_materialization)
    page = module.query_outcomes(OutcomeFilters(horizon=1))

    assert switched is True
    assert page.total == 1
    assert len(page.items) == 1
    assert page.items[0].outcome_run_id == first_run.id
    factory.kw["bind"].dispose()


def test_summary_uses_one_materialized_snapshot_during_publish_switch(
    tmp_path,
    monkeypatch,
):
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'summary-published-snapshot.db'}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        source = add_batch(session, date(2026, 8, 27))
        first_evaluation = add_batch(session, date(2026, 9, 2), active=True)
        next_evaluation = add_batch(session, date(2026, 9, 3))
        candidate = add_candidate(session, source, "SH", "600001")
        first_run = add_completed_run(session, first_evaluation)
        next_run = OutcomeRun(
            evaluation_batch_id=next_evaluation.id,
            calculation_version="outcome-v1",
            rule_version=source.rule_version,
            status="RUNNING",
        )
        session.add(next_run)
        session.flush()
        for run, return_rate in ((first_run, 1.0), (next_run, 9.0)):
            session.add(
                CandidateOutcome(
                    candidate_result_id=candidate.id,
                    source_batch_id=source.id,
                    evaluation_batch_id=run.evaluation_batch_id,
                    outcome_run_id=run.id,
                    source_trade_date=source.trade_date,
                    rule_version=source.rule_version,
                    horizon_trading_days=1,
                    status="COMPLETED",
                    return_rate=return_rate,
                    mfe=return_rate,
                    mae=-return_rate,
                    calculation_version="outcome-v1",
                )
            )
        session.commit()

    module = CandidateOutcomeModule(factory)
    original_materialize = module._published_run_ids

    def publish_after_materialization(session, filters):
        run_ids = original_materialize(session, filters)
        with factory.begin() as writer:
            writer.get(OutcomeRun, next_run.id).status = "COMPLETED"
        return run_ids

    monkeypatch.setattr(module, "_published_run_ids", publish_after_materialization)
    summary = module.summarize_outcomes(OutcomeFilters(horizon=1))

    assert summary.total == 1
    assert summary.completed == 1
    assert summary.mean_return_rate == pytest.approx(1.0)
    assert summary.median_return_rate == pytest.approx(1.0)
    assert summary.mean_mfe == pytest.approx(1.0)
    assert summary.max_drawdown_approx == pytest.approx(-1.0)
    factory.kw["bind"].dispose()


def test_all_read_models_hide_outcome_attached_to_a_different_candidate_rule(
    session_factory,
):
    with session_factory() as session:
        candidate_batch = add_batch(
            session,
            date(2026, 8, 27),
            rule_version="rules-v1",
        )
        evaluation_batch = add_batch(
            session,
            date(2026, 9, 3),
            rule_version="rules-v2",
            active=True,
        )
        candidate = add_candidate(session, candidate_batch, "SH", "600001")
        run = OutcomeRun(
            evaluation_batch_id=evaluation_batch.id,
            calculation_version="outcome-v1",
            rule_version="rules-v2",
            status="COMPLETED",
            expected_count=1,
            completed_count=1,
        )
        session.add(run)
        session.flush()
        session.add(
            CandidateOutcome(
                candidate_result_id=candidate.id,
                source_batch_id=candidate_batch.id,
                evaluation_batch_id=evaluation_batch.id,
                outcome_run_id=run.id,
                source_trade_date=candidate_batch.trade_date,
                rule_version="rules-v2",
                horizon_trading_days=1,
                status="COMPLETED",
                return_rate=10,
                calculation_version="outcome-v1",
            )
        )
        session.commit()

    module = CandidateOutcomeModule(session_factory)

    assert module.query_outcomes(OutcomeFilters()).total == 0
    assert module.summarize_outcomes(OutcomeFilters()).total == 0
    assert module.get_candidate_outcomes(candidate.id) == []
    assert module.get_batch_statuses(candidate_batch.id) == {
        candidate.id: "PENDING"
    }


def test_median_returns_none_if_the_materialized_snapshot_has_no_rows(
    session_factory,
):
    module = CandidateOutcomeModule(session_factory)
    with session_factory() as session:
        assert module._median_return_rate(
            session,
            [CandidateOutcome.id == -1],
            completed_return_count=1,
        ) is None


def test_query_and_summary_apply_rule_version_and_source_date_filters(session_factory):
    with session_factory() as session:
        dates = seed_calendar(session)
        older = add_batch(
            session, date(2026, 8, 27), rule_version="rules-old"
        )
        newer = add_batch(
            session, date(2026, 8, 28), rule_version="rules-new"
        )
        old_evaluation = add_batch(
            session,
            date(2026, 9, 2),
            rule_version="rules-old",
        )
        new_evaluation = add_batch(
            session,
            date(2026, 9, 3),
            rule_version="rules-new",
            active=True,
        )
        first = add_candidate(session, older, "SH", "600001")
        second = add_candidate(session, newer, "SZ", "000001")
        for trade_date in dates[:4]:
            add_price(
                session,
                old_evaluation.id,
                first.market,
                first.stock_code,
                trade_date,
            )
        for trade_date in dates:
            add_price(
                session,
                new_evaluation.id,
                second.market,
                second.stock_code,
                trade_date,
            )
            add_price(
                session,
                new_evaluation.id,
                first.market,
                first.stock_code,
                trade_date,
            )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    module.evaluate_due_outcomes(old_evaluation.id)
    module.evaluate_due_outcomes(new_evaluation.id)

    old_page = module.query_outcomes(OutcomeFilters(rule_version="rules-old", horizon=1))
    new_summary = module.summarize_outcomes(
        OutcomeFilters(rule_version="rules-new", horizon=1)
    )
    dated_page = module.query_outcomes(
        OutcomeFilters(
            horizon=1,
            date_from=date(2026, 8, 28),
            date_to=date(2026, 8, 28),
        )
    )
    dated_summary = module.summarize_outcomes(
        OutcomeFilters(
            horizon=1,
            date_from=date(2026, 8, 27),
            date_to=date(2026, 8, 27),
        )
    )

    assert [item.candidate_result_id for item in old_page.items] == [first.id]
    assert new_summary.total == 1
    assert new_summary.completed == 1
    assert new_summary.max_drawdown_approx == pytest.approx(-10)
    assert [item.candidate_result_id for item in dated_page.items] == [second.id]
    assert dated_summary.total == 1
    assert dated_summary.completed == 1
    assert dated_summary.max_drawdown_approx == pytest.approx(-10)


def test_latest_trading_days_keeps_all_candidates_and_precedes_outcome_filters(
    session_factory,
):
    with session_factory() as session:
        oldest = add_batch(session, date(2026, 8, 26))
        middle = add_batch(session, date(2026, 8, 27))
        newest = add_batch(session, date(2026, 8, 28), active=True)
        session.add_all(
            [
                TradeCalendar(market="CN", trade_date=value, is_open=True)
                for value in (
                    date(2026, 8, 26),
                    date(2026, 8, 27),
                    date(2026, 8, 28),
                )
            ]
        )
        candidates = [
            add_candidate(session, oldest, "SH", "600001"),
            add_candidate(session, middle, "SH", "600002"),
            add_candidate(session, newest, "SH", "600003"),
            add_candidate(session, newest, "SZ", "000001"),
        ]
        published_run = add_completed_run(session, newest)
        for trade_date in (
            date(2026, 8, 26),
            date(2026, 8, 27),
            date(2026, 8, 28),
        ):
            add_price(
                session,
                newest.id,
                candidates[2].market,
                candidates[2].stock_code,
                trade_date,
            )
        session.add_all(
            [
                CandidateOutcome(
                    candidate_result_id=candidate.id,
                    source_batch_id=batch.id,
                    evaluation_batch_id=newest.id,
                    outcome_run_id=published_run.id,
                    source_trade_date=batch.trade_date,
                    rule_version=batch.rule_version,
                    horizon_trading_days=horizon,
                    status=status,
                    calculation_version="outcome-v1",
                )
                for candidate, batch, horizon, status in (
                    (candidates[0], oldest, 1, "COMPLETED"),
                    (candidates[1], middle, 1, "COMPLETED"),
                    (candidates[2], newest, 1, "PENDING"),
                    (candidates[3], newest, 3, "COMPLETED"),
                )
            ]
        )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    recent = OutcomeFilters(latest_trading_days=2)
    page = module.query_outcomes(recent)
    summary = module.summarize_outcomes(recent)

    assert page.total == 3
    assert {item.source_trade_date for item in page.items} == {
        date(2026, 8, 27),
        date(2026, 8, 28),
    }
    assert [item.candidate_result_id for item in page.items[:2]] == [
        candidates[2].id,
        candidates[3].id,
    ]
    assert summary.total == page.total
    assert summary.completed == 2
    assert summary.pending == 1
    assert summary.data_date == page.data_date == date(2026, 8, 28)

    completed_one_day = OutcomeFilters(
        latest_trading_days=1,
        horizon=1,
        status="COMPLETED",
    )
    assert module.query_outcomes(completed_one_day).total == 0
    assert module.summarize_outcomes(completed_one_day).total == 0


def test_latest_trading_days_uses_authoritative_calendar_when_candidates_are_sparse(
    session_factory,
):
    calendar_dates = [date(2026, 8, day) for day in range(25, 30)]
    with session_factory() as session:
        batches = [
            add_batch(
                session,
                trade_date,
                active=trade_date == calendar_dates[-1],
            )
            for trade_date in (calendar_dates[0], calendar_dates[-1])
        ]
        candidates = [
            add_candidate(session, batch, "SH", f"60000{index}")
            for index, batch in enumerate(batches, start=1)
        ]
        published_run = add_completed_run(session, batches[-1])
        session.add_all(
            [
                TradeCalendar(market="CN", trade_date=value, is_open=True)
                for value in calendar_dates
            ]
        )
        session.add_all(
            [
                CandidateOutcome(
                    candidate_result_id=candidate.id,
                    source_batch_id=batch.id,
                    evaluation_batch_id=batches[-1].id,
                    outcome_run_id=published_run.id,
                    source_trade_date=batch.trade_date,
                    rule_version=batch.rule_version,
                    horizon_trading_days=1,
                    status="COMPLETED",
                    calculation_version="outcome-v1",
                )
                for candidate, batch in zip(candidates, batches, strict=True)
            ]
        )
        for trade_date in calendar_dates:
            add_price(
                session,
                batches[-1].id,
                candidates[-1].market,
                candidates[-1].stock_code,
                trade_date,
            )
        session.commit()

    filters = OutcomeFilters(latest_trading_days=2)
    module = CandidateOutcomeModule(session_factory)
    page = module.query_outcomes(filters)
    summary = module.summarize_outcomes(filters)

    assert [item.source_trade_date for item in page.items] == [calendar_dates[-1]]
    assert summary.total == page.total == 1
    assert summary.data_date == page.data_date == calendar_dates[-1]


def test_latest_trading_days_applies_date_bounds_to_authoritative_calendar_window(
    session_factory,
):
    calendar_dates = [date(2026, 8, day) for day in range(25, 30)]
    with session_factory() as session:
        batches = [
            add_batch(
                session,
                trade_date,
                active=trade_date == calendar_dates[-1],
            )
            for trade_date in calendar_dates
        ]
        published_run = add_completed_run(session, batches[-1])
        session.add_all(
            [
                TradeCalendar(market="CN", trade_date=value, is_open=True)
                for value in calendar_dates
            ]
        )
        for index, batch in enumerate(batches, start=1):
            candidate = add_candidate(session, batch, "SH", f"60001{index}")
            session.add(
                CandidateOutcome(
                    candidate_result_id=candidate.id,
                    source_batch_id=batch.id,
                    evaluation_batch_id=batches[-1].id,
                    outcome_run_id=published_run.id,
                    source_trade_date=batch.trade_date,
                    rule_version=batch.rule_version,
                    horizon_trading_days=1,
                    status="COMPLETED",
                    calculation_version="outcome-v1",
                )
            )
        for trade_date in calendar_dates:
            add_price(
                session,
                batches[-1].id,
                "SH",
                "699999",
                trade_date,
            )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    to_filters = OutcomeFilters(
        latest_trading_days=2,
        date_from=calendar_dates[0],
        date_to=calendar_dates[2],
    )
    from_filters = OutcomeFilters(
        latest_trading_days=5,
        date_from=calendar_dates[3],
    )

    to_page = module.query_outcomes(to_filters)
    to_summary = module.summarize_outcomes(to_filters)
    from_page = module.query_outcomes(from_filters)
    from_summary = module.summarize_outcomes(from_filters)

    assert [item.source_trade_date for item in to_page.items] == calendar_dates[1:3][::-1]
    assert to_summary.total == to_page.total == 2
    assert to_summary.data_date == to_page.data_date == calendar_dates[2]
    assert [item.source_trade_date for item in from_page.items] == calendar_dates[3:][::-1]
    assert from_summary.total == from_page.total == 2
    assert from_summary.data_date == from_page.data_date == calendar_dates[-1]


@pytest.mark.parametrize("value", [0, 251, True])
def test_latest_trading_days_rejects_values_outside_one_to_250(
    session_factory,
    value,
):
    with pytest.raises(ValueError, match="latest_trading_days"):
        OutcomeFilters(latest_trading_days=value)


def test_evaluation_select_count_does_not_grow_per_candidate(session_factory):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        for index in range(20):
            code = f"60{index:04d}"
            add_candidate(session, source, "SH", code)
            for trade_date in dates:
                add_price(session, evaluation.id, "SH", code, trade_date)
        session.commit()

    select_count = 0

    def count_selects(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    factory_engine = session_factory.kw["bind"]
    event.listen(factory_engine, "before_cursor_execute", count_selects)
    try:
        result = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    finally:
        event.remove(factory_engine, "before_cursor_execute", count_selects)

    assert result.expected_count == 60
    assert select_count <= 17


def test_failed_source_batch_candidates_are_excluded_from_outcomes_and_counters(
    session_factory,
):
    with session_factory() as session:
        dates = seed_calendar(session)
        valid_source = add_batch(session, date(2026, 8, 27))
        failed_source = add_batch(
            session, date(2026, 8, 27), status="FAILED", rule_version="failed-rules"
        )
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        valid = add_candidate(session, valid_source, "SH", "600000")
        invalid = add_candidate(session, failed_source, "SZ", "000001")
        for candidate in (valid, invalid):
            for trade_date in dates:
                add_price(
                    session,
                    evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                )
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    result = module.evaluate_due_outcomes(evaluation.id)

    assert (result.expected_count, result.completed_count) == (3, 3)
    assert module.get_candidate_outcomes(invalid.id) == []
    assert module.query_outcomes(OutcomeFilters(rule_version="failed-rules")).total == 0
    assert module.summarize_outcomes(
        OutcomeFilters(rule_version="failed-rules")
    ).total == 0


def test_loads_only_exact_candidate_price_keys_not_unrelated_batch_history(
    session_factory,
):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        for trade_date in dates:
            add_price(session, evaluation.id, "SH", "600000", trade_date)
        for index in range(100):
            for trade_date in dates:
                add_price(
                    session,
                    evaluation.id,
                    "SZ",
                    f"00{index:04d}",
                    trade_date,
                )
        session.commit()

    loaded_keys = []

    def record_price_load(price, _context):
        loaded_keys.append((price.market, price.stock_code, price.trade_date))

    event.listen(DailyPrice, "load", record_price_load)
    try:
        result = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    finally:
        event.remove(DailyPrice, "load", record_price_load)

    assert result.completed_count == 3
    assert set(loaded_keys) == {
        ("SH", "600000", trade_date) for trade_date in dates
    }
    assert len(CandidateOutcomeModule(session_factory).get_candidate_outcomes(candidate.id)) == 3


def test_terminal_candidate_skips_price_loading_and_calculation_while_pending_runs(
    session_factory, monkeypatch
):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        terminal = add_candidate(session, source, "SH", "600001")
        pending = add_candidate(session, source, "SH", "600002")
        now = outcome_application.datetime.now(outcome_application.UTC)
        for horizon in (1, 3, 5):
            session.add(
                CandidateOutcome(
                    candidate_result_id=terminal.id,
                    source_batch_id=source.id,
                    evaluation_batch_id=evaluation.id,
                    source_trade_date=source.trade_date,
                    rule_version=source.rule_version,
                    horizon_trading_days=horizon,
                    reference_trade_date=dates[0],
                    evaluation_trade_date=dates[horizon - 1],
                    reference_price=99,
                    evaluation_price=99,
                    return_rate=0,
                    mfe=0,
                    mae=0,
                    status="COMPLETED",
                    calculation_version="outcome-v1",
                    created_at=now,
                    updated_at=now,
                )
            )
        for candidate, open_price in ((terminal, 99), (pending, 10)):
            for trade_date in dates:
                add_price(
                    session,
                    evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                    open_price=open_price,
                    high=open_price + 1,
                    low=open_price - 1,
                    close=open_price,
                )
        session.commit()

    calculated_reference_prices = []
    original = outcome_application.calculate_outcome

    def record_calculation(bars, horizon):
        calculated_reference_prices.append(bars[0].open_raw)
        return original(bars, horizon)

    loaded_codes = []

    def record_price_load(price, _context):
        loaded_codes.append(price.stock_code)

    monkeypatch.setattr(outcome_application, "calculate_outcome", record_calculation)
    event.listen(DailyPrice, "load", record_price_load)
    try:
        result = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    finally:
        event.remove(DailyPrice, "load", record_price_load)

    assert result.expected_count == 6
    assert result.completed_count == 6
    assert calculated_reference_prices == [99, 99, 99, 10, 10, 10]
    assert set(loaded_codes) == {terminal.stock_code, pending.stock_code}


def test_summary_uses_outcome_only_sql_and_exact_median_for_large_sample(session_factory):
    with session_factory() as session:
        source = add_batch(session, date(2026, 8, 27))
        published_run = add_completed_run(session, source)
        now = outcome_application.datetime.now(outcome_application.UTC)
        for index in range(41):
            candidate = add_candidate(session, source, "SH", f"60{index:04d}")
            session.add(
                CandidateOutcome(
                    candidate_result_id=candidate.id,
                    source_batch_id=source.id,
                    evaluation_batch_id=source.id,
                    outcome_run_id=published_run.id,
                    source_trade_date=source.trade_date,
                    rule_version="rules-v1",
                    horizon_trading_days=1,
                    reference_trade_date=date(2026, 8, 28),
                    evaluation_trade_date=date(2026, 8, 28),
                    reference_price=10,
                    evaluation_price=10 + index / 10,
                    return_rate=float(index - 20),
                    mfe=float(index),
                    mae=float(-index),
                    status="COMPLETED",
                    calculation_version="outcome-v1",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()

    statements = []

    def capture_selects(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.lower())

    engine = session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_selects)
    try:
        summary = CandidateOutcomeModule(session_factory).summarize_outcomes(
            OutcomeFilters(horizon=1)
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_selects)

    assert summary.total == 41
    assert summary.median_return_rate == 0
    assert summary.mean_return_rate == pytest.approx(0)
    assert summary.positive_return_ratio == pytest.approx(20 / 41)
    assert all(" join candidate_result" not in statement for statement in statements)
    assert all("stock_basic" not in statement for statement in statements)
    assert len(statements) <= 4


def test_calendar_shorter_than_horizon_keeps_later_outcomes_pending(session_factory):
    with session_factory() as session:
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidate = add_candidate(session, source, "SH", "600000")
        calendar_dates = [date(2026, 8, 28), date(2026, 8, 31)]
        session.add_all(
            [
                TradeCalendar(market="CN", trade_date=value, is_open=True)
                for value in calendar_dates
            ]
        )
        for trade_date in calendar_dates:
            add_price(session, evaluation.id, "SH", "600000", trade_date)
        session.commit()

    run = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    outcomes = CandidateOutcomeModule(session_factory).get_candidate_outcomes(candidate.id)

    assert [item.status for item in outcomes] == ["COMPLETED", "PENDING", "PENDING"]
    assert (run.completed_count, run.pending_count) == (1, 2)


def test_rejects_unready_batch_and_missing_candidate(session_factory):
    with session_factory() as session:
        batch = add_batch(session, date(2026, 8, 31), status="BUILDING")
        unscanned = add_candidate(session, batch, "SH", "600000")
        ready_with_gaps = add_batch(
            session, date(2026, 9, 1), status="READY_WITH_GAPS"
        )
        active_building = add_batch(session, date(2026, 9, 2), status="BUILDING")
        session.commit()

    module = CandidateOutcomeModule(session_factory)
    assert module.get_candidate_outcomes(unscanned.id) == []
    with pytest.raises(OutcomeBatchNotReadyError):
        module.evaluate_due_outcomes(999)
    with pytest.raises(OutcomeBatchNotReadyError):
        module.evaluate_due_outcomes(batch.id)
    assert module.evaluate_due_outcomes(ready_with_gaps.id).status == "COMPLETED"
    with session_factory() as session:
        session.get(DataBatch, active_building.id).is_active = True
        session.commit()
    with pytest.raises(OutcomeBatchNotReadyError):
        module.evaluate_due_outcomes(active_building.id)
    with pytest.raises(CandidateOutcomeNotFoundError):
        module.get_candidate_outcomes(999)


def test_batch_statuses_cover_all_aggregate_states_in_one_query(session_factory):
    with session_factory() as session:
        batch = add_batch(session, date(2026, 8, 27), active=True)
        published_run = add_completed_run(session, batch)
        no_rows = add_candidate(session, batch, "SH", "600001")
        pending = add_candidate(session, batch, "SH", "600002")
        completed = add_candidate(session, batch, "SH", "600003")
        unavailable = add_candidate(session, batch, "SH", "600004")
        partial = add_candidate(session, batch, "SH", "600005")
        for candidate, statuses in (
            (pending, ("PENDING", "PENDING", "PENDING")),
            (completed, ("COMPLETED", "COMPLETED", "COMPLETED")),
            (unavailable, ("UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE")),
            (partial, ("COMPLETED", "UNAVAILABLE", "PENDING")),
        ):
            session.add_all(
                [
                    CandidateOutcome(
                        candidate_result_id=candidate.id,
                        source_batch_id=batch.id,
                        evaluation_batch_id=(
                            batch.id if status != "PENDING" else None
                        ),
                        outcome_run_id=published_run.id,
                        source_trade_date=batch.trade_date,
                        rule_version=batch.rule_version,
                        horizon_trading_days=horizon,
                        status=status,
                        calculation_version="outcome-v1",
                    )
                    for horizon, status in zip((1, 3, 5), statuses, strict=True)
                ]
            )
        session.commit()

    statements = []

    def capture_selects(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_selects)
    try:
        statuses = CandidateOutcomeModule(session_factory).get_batch_statuses(batch.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture_selects)

    assert statuses == {
        no_rows.id: "PENDING",
        pending.id: "PENDING",
        completed.id: "COMPLETED",
        unavailable.id: "UNAVAILABLE",
        partial.id: "PARTIAL",
    }
    assert len(statements) == 1


def test_price_loading_uses_two_chunks_when_more_than_250_keys_are_required(
    session_factory,
):
    with session_factory() as session:
        dates = seed_calendar(session)
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidates = [
            add_candidate(session, source, "SH", f"60{index:04d}")
            for index in range(51)
        ]
        for candidate in candidates:
            for trade_date in dates:
                add_price(
                    session,
                    evaluation.id,
                    candidate.market,
                    candidate.stock_code,
                    trade_date,
                )
        session.commit()

    price_queries = []

    def capture_price_queries(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.lower()
        if (
            normalized.lstrip().startswith("select")
            and "from daily_price" in normalized
            and "daily_price.market" in normalized
        ):
            price_queries.append(statement)

    engine = session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture_price_queries)
    try:
        run = CandidateOutcomeModule(session_factory).evaluate_due_outcomes(evaluation.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture_price_queries)

    assert run.completed_count == 153
    assert run.unavailable_count == 0
    assert len(price_queries) == 2


@pytest.mark.parametrize("missing_batch_id", [None, 999])
def test_batch_statuses_return_empty_for_empty_or_missing_batch(
    session_factory, missing_batch_id
):
    if missing_batch_id is None:
        with session_factory() as session:
            batch = add_batch(session, date(2026, 8, 27))
            session.commit()
        batch_id = batch.id
    else:
        batch_id = missing_batch_id

    assert CandidateOutcomeModule(session_factory).get_batch_statuses(batch_id) == {}


@pytest.mark.parametrize(
    "statuses",
    [
        ("COMPLETED",),
        ("COMPLETED", "COMPLETED"),
        ("UNAVAILABLE",),
        ("PENDING", "COMPLETED"),
    ],
)
def test_batch_statuses_with_one_or_two_horizons_are_partial(
    session_factory, statuses
):
    with session_factory() as session:
        batch = add_batch(session, date(2026, 8, 27))
        published_run = add_completed_run(session, batch)
        candidate = add_candidate(session, batch, "SH", "600001")
        session.add_all(
            [
                CandidateOutcome(
                    candidate_result_id=candidate.id,
                    source_batch_id=batch.id,
                    evaluation_batch_id=(
                        batch.id if status != "PENDING" else None
                    ),
                    outcome_run_id=published_run.id,
                    source_trade_date=batch.trade_date,
                    rule_version=batch.rule_version,
                    horizon_trading_days=horizon,
                    status=status,
                    calculation_version="outcome-v1",
                )
                for horizon, status in zip((1, 3), statuses, strict=False)
            ]
        )
        session.commit()

    assert CandidateOutcomeModule(session_factory).get_batch_statuses(batch.id) == {
        candidate.id: "PARTIAL"
    }
