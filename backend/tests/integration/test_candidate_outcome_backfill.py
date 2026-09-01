import json
from datetime import date
from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import event, func, select

import scripts.backfill_candidate_outcomes as backfill_script
from app.application.candidate_outcomes import CandidateOutcomeModule
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
    TradeCalendar,
)
from scripts.backfill_candidate_outcomes import main, run_backfill


@pytest.fixture
def session_factory():
    factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(factory.kw["bind"])
    yield factory
    factory.kw["bind"].dispose()


def add_batch(session, trade_date, *, status="READY", source="test", active=False):
    batch = DataBatch(
        source=source,
        trade_date=trade_date,
        status=status,
        completeness_rate=1,
        rule_version="rules-v1",
        is_active=active,
    )
    session.add(batch)
    session.flush()
    return batch


def add_candidate(session, batch, code):
    candidate = CandidateResult(
        batch_id=batch.id,
        market="SH",
        stock_code=code,
        score=1,
        reasons=[],
    )
    session.add(candidate)
    session.flush()
    return candidate


def add_price(session, batch, code, trade_date):
    session.add(
        DailyPrice(
            batch_id=batch.id,
            market="SH",
            stock_code=code,
            trade_date=trade_date,
            adjustment="raw",
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=100,
            amount=1000,
        )
    )


def add_authoritative_calendar(session, open_dates):
    start_date = min(open_dates)
    end_date = max(open_dates)
    session.add_all(
        [
            TradeCalendar(
                market="CN",
                trade_date=date.fromordinal(ordinal),
                is_open=date.fromordinal(ordinal) in open_dates,
            )
            for ordinal in range(start_date.toordinal(), end_date.toordinal() + 1)
        ]
    )


def test_dry_run_is_read_only_and_reports_valid_scope(session_factory):
    with session_factory() as session:
        ready = add_batch(session, date(2026, 8, 27))
        gaps = add_batch(session, date(2026, 8, 28), status="READY_WITH_GAPS")
        failed = add_batch(session, date(2026, 8, 29), status="FAILED")
        ready_candidate = add_candidate(session, ready, "600001")
        add_candidate(session, gaps, "600002")
        failed_candidate = add_candidate(session, failed, "600003")
        session.add_all(
            [
                CandidateOutcome(
                    candidate_result_id=ready_candidate.id,
                    source_batch_id=ready.id,
                    source_trade_date=ready.trade_date,
                    rule_version=ready.rule_version,
                    horizon_trading_days=1,
                    status="PENDING",
                    calculation_version="outcome-v1",
                ),
                CandidateOutcome(
                    candidate_result_id=failed_candidate.id,
                    source_batch_id=failed.id,
                    source_trade_date=failed.trade_date,
                    rule_version=failed.rule_version,
                    horizon_trading_days=1,
                    status="COMPLETED",
                    calculation_version="outcome-v1",
                ),
            ]
        )
        session.commit()
        before = (
            session.scalar(select(func.count(CandidateOutcome.id))),
            session.scalar(select(func.count(OutcomeRun.id))),
        )

    output = StringIO()
    error_output = StringIO()
    exit_code = run_backfill(
        session_factory,
        dry_run=True,
        calculation_version="outcome-v1",
        output=output,
        error_output=error_output,
    )

    assert exit_code == 0
    assert error_output.getvalue() == ""
    assert json.loads(output.getvalue()) == {
        "candidate_count": 2,
        "current_published_outcome_rows": 0,
        "current_published_snapshot_count": 0,
        "current_published_status_counts": {
            "COMPLETED": 0,
            "PENDING": 0,
            "UNAVAILABLE": 0,
        },
        "evaluation_batch_ids": [ready.id, gaps.id],
        "final_logical_outcome_rows": 6,
        "mode": "dry-run",
        "projected_cohorts": [
            {
                "completed_count": 0,
                "evaluation_batch_id": ready.id,
                "expected_count": 3,
                "pending_count": 3,
                "rule_version": "rules-v1",
                "source": "test",
                "unavailable_count": 0,
            },
            {
                "completed_count": 0,
                "evaluation_batch_id": gaps.id,
                "expected_count": 6,
                "pending_count": 6,
                "rule_version": "rules-v1",
                "source": "test",
                "unavailable_count": 0,
            },
        ],
        "projected_totals": {
            "completed_count": 0,
            "expected_count": 9,
            "pending_count": 9,
            "unavailable_count": 0,
        },
        "valid_batch_count": 2,
    }
    with session_factory() as session:
        after = (
            session.scalar(select(func.count(CandidateOutcome.id))),
            session.scalar(select(func.count(OutcomeRun.id))),
        )
    assert after == before


def test_dry_run_projects_each_planned_cohort_and_matches_real_run(session_factory):
    trading_dates = [
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    with session_factory() as session:
        source = add_batch(session, date(2026, 8, 27))
        evaluation = add_batch(session, date(2026, 9, 3), active=True)
        candidate = add_candidate(session, source, "600001")
        add_authoritative_calendar(session, set(trading_dates))
        for trade_date in trading_dates:
            add_price(session, evaluation, candidate.stock_code, trade_date)
        session.commit()
        before = (
            session.scalar(select(func.count(CandidateOutcome.id))),
            session.scalar(select(func.count(OutcomeRun.id))),
        )

    output = StringIO()
    error_output = StringIO()
    assert run_backfill(
        session_factory,
        dry_run=True,
        calculation_version="outcome-v1",
        output=output,
        error_output=error_output,
    ) == 0

    summary = json.loads(output.getvalue())
    assert error_output.getvalue() == ""
    assert summary["projected_totals"] == {
        "completed_count": 3,
        "expected_count": 6,
        "pending_count": 3,
        "unavailable_count": 0,
    }
    assert summary["final_logical_outcome_rows"] == 3
    assert "expected_outcome_rows" not in summary
    assert summary["projected_cohorts"] == [
        {
            "completed_count": 0,
            "evaluation_batch_id": source.id,
            "expected_count": 3,
            "pending_count": 3,
            "rule_version": "rules-v1",
            "source": "test",
            "unavailable_count": 0,
        },
        {
            "completed_count": 3,
            "evaluation_batch_id": evaluation.id,
            "expected_count": 3,
            "pending_count": 0,
            "rule_version": "rules-v1",
            "source": "test",
            "unavailable_count": 0,
        },
    ]
    with session_factory() as session:
        after_dry_run = (
            session.scalar(select(func.count(CandidateOutcome.id))),
            session.scalar(select(func.count(OutcomeRun.id))),
        )
    assert after_dry_run == before

    assert run_backfill(
        session_factory,
        dry_run=False,
        calculation_version="outcome-v1",
        output=StringIO(),
        error_output=StringIO(),
    ) == 0
    with session_factory() as session:
        real_run = session.scalar(
            select(OutcomeRun).where(
                OutcomeRun.evaluation_batch_id == evaluation.id,
                OutcomeRun.rule_version == "rules-v1",
                OutcomeRun.status == "COMPLETED",
            )
        )
        projected_final = summary["projected_cohorts"][-1]
        assert (
            real_run.expected_count,
            real_run.completed_count,
            real_run.unavailable_count,
            real_run.pending_count,
        ) == (
            projected_final["expected_count"],
            projected_final["completed_count"],
            projected_final["unavailable_count"],
            projected_final["pending_count"],
        )


def test_dry_run_projection_failure_is_redacted_and_does_not_write(
    session_factory,
    monkeypatch,
):
    with session_factory() as session:
        add_batch(session, date(2026, 8, 27))
        session.commit()

    def fail_projection(_self, _evaluation_batch_id):
        raise RuntimeError(
            "SELECT secret FROM /Users/private.db https://secret.invalid"
        )

    monkeypatch.setattr(
        CandidateOutcomeModule,
        "plan_due_outcomes",
        fail_projection,
        raising=False,
    )
    output = StringIO()
    error_output = StringIO()
    exit_code = run_backfill(
        session_factory,
        dry_run=True,
        calculation_version="outcome-v1",
        output=output,
        error_output=error_output,
    )

    assert exit_code == 2
    assert output.getvalue() == ""
    error = json.loads(error_output.getvalue())
    assert error == {
        "error_type": "RuntimeError",
        "event": "candidate_outcome_backfill_failed",
        "message": "回填计划读取失败",
        "phase": "planning",
    }
    assert "SELECT" not in error_output.getvalue()
    assert "/Users" not in error_output.getvalue()
    assert "https://" not in error_output.getvalue()
    with session_factory() as session:
        assert session.scalar(select(func.count(OutcomeRun.id))) == 0
        assert session.scalar(select(func.count(CandidateOutcome.id))) == 0


def test_dry_run_current_published_counts_exclude_cross_source_rows(
    session_factory,
):
    with session_factory() as session:
        demo_source = add_batch(
            session,
            date(2026, 8, 27),
            source="demo-v1",
        )
        real_evaluation = add_batch(
            session,
            date(2026, 8, 28),
            source="tencent-sina-v1",
            active=True,
        )
        candidate = add_candidate(session, demo_source, "600001")
        run = OutcomeRun(
            evaluation_batch_id=real_evaluation.id,
            calculation_version="outcome-v1",
            rule_version="rules-v1",
            status="COMPLETED",
            expected_count=1,
            completed_count=1,
        )
        session.add(run)
        session.flush()
        session.add(
            CandidateOutcome(
                candidate_result_id=candidate.id,
                source_batch_id=demo_source.id,
                evaluation_batch_id=real_evaluation.id,
                outcome_run_id=run.id,
                source_trade_date=demo_source.trade_date,
                rule_version="rules-v1",
                horizon_trading_days=1,
                status="COMPLETED",
                calculation_version="outcome-v1",
            )
        )
        session.commit()

    output = StringIO()
    assert run_backfill(
        session_factory,
        dry_run=True,
        calculation_version="outcome-v1",
        output=output,
        error_output=StringIO(),
    ) == 0

    summary = json.loads(output.getvalue())
    assert summary["current_published_outcome_rows"] == 0
    assert summary["current_published_status_counts"] == {
        "COMPLETED": 0,
        "PENDING": 0,
        "UNAVAILABLE": 0,
    }


def test_dry_run_planning_does_not_create_file_lock(tmp_path: Path):
    database_path = tmp_path / "dry-run.db"
    factory = create_sqlite_session_factory(
        f"sqlite+pysqlite:///{database_path}"
    )
    Base.metadata.create_all(factory.kw["bind"])
    with factory() as session:
        add_batch(session, date(2026, 8, 27))
        session.commit()

    assert run_backfill(
        factory,
        dry_run=True,
        calculation_version="outcome-v1",
        output=StringIO(),
        error_output=StringIO(),
    ) == 0

    assert not Path(f"{database_path}.candidate-outcomes.lock").exists()
    factory.kw["bind"].dispose()


def test_cli_help_does_not_open_default_database():
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == 0


def test_real_backfill_runs_valid_batches_in_order_and_is_idempotent(session_factory):
    trading_dates = [
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    with session_factory() as session:
        source = add_batch(session, date(2026, 8, 27))
        first_evaluation = add_batch(session, date(2026, 8, 28))
        final_evaluation = add_batch(session, date(2026, 9, 3), status="READY_WITH_GAPS")
        failed = add_batch(session, date(2026, 9, 4), status="FAILED")
        building = add_batch(session, date(2026, 9, 5), status="BUILDING")
        candidate = add_candidate(session, source, "600001")
        failed_candidate = add_candidate(session, failed, "600002")
        building_candidate = add_candidate(session, building, "600003")
        add_authoritative_calendar(session, set(trading_dates))
        add_price(session, first_evaluation, candidate.stock_code, trading_dates[0])
        for value in trading_dates:
            add_price(session, final_evaluation, candidate.stock_code, value)
        session.commit()

    output = StringIO()
    error_output = StringIO()
    exit_code = run_backfill(
        session_factory,
        dry_run=False,
        calculation_version="outcome-v1",
        output=output,
        error_output=error_output,
    )

    assert exit_code == 0
    assert error_output.getvalue() == ""
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [row["evaluation_batch_id"] for row in rows] == [
        source.id,
        first_evaluation.id,
        final_evaluation.id,
    ]
    assert all(row["status"] == "COMPLETED" for row in rows)
    with session_factory() as session:
        outcomes = session.scalars(
            select(CandidateOutcome)
            .where(
                CandidateOutcome.candidate_result_id == candidate.id,
                CandidateOutcome.evaluation_batch_id == final_evaluation.id,
            )
            .order_by(CandidateOutcome.horizon_trading_days)
        ).all()
        assert [item.horizon_trading_days for item in outcomes] == [1, 3, 5]
        assert [item.status for item in outcomes] == [
            "COMPLETED",
            "COMPLETED",
            "COMPLETED",
        ]
        assert session.scalar(
            select(func.count(CandidateOutcome.id)).where(
                CandidateOutcome.candidate_result_id.in_(
                    (failed_candidate.id, building_candidate.id)
                )
            )
        ) == 0
        counts_before = (
            session.scalar(select(func.count(OutcomeRun.id))),
            session.scalar(select(func.count(CandidateOutcome.id))),
        )

    repeated_output = StringIO()
    assert run_backfill(
        session_factory,
        dry_run=False,
        calculation_version="outcome-v1",
        output=repeated_output,
        error_output=StringIO(),
    ) == 0
    with session_factory() as session:
        assert (
            session.scalar(select(func.count(OutcomeRun.id))),
            session.scalar(select(func.count(CandidateOutcome.id))),
        ) == counts_before


def test_backfill_keeps_each_evaluation_batch_within_its_source(session_factory):
    trading_dates = [
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    with session_factory() as session:
        demo_batch = add_batch(
            session,
            date(2026, 8, 27),
            source="demo-v1",
        )
        real_batch = add_batch(
            session,
            date(2026, 8, 27),
            source="tencent-sina-v1",
        )
        real_evaluation = add_batch(
            session,
            trading_dates[-1],
            source="tencent-sina-v1",
            active=True,
        )
        demo_candidate = add_candidate(session, demo_batch, "600001")
        real_candidate = add_candidate(session, real_batch, "600002")
        add_authoritative_calendar(session, set(trading_dates))
        for trade_date in trading_dates:
            add_price(session, real_evaluation, real_candidate.stock_code, trade_date)
            add_price(session, real_evaluation, demo_candidate.stock_code, trade_date)
        session.commit()

    output = StringIO()
    assert run_backfill(
        session_factory,
        dry_run=False,
        calculation_version="outcome-v1",
        output=output,
        error_output=StringIO(),
    ) == 0

    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [row["evaluation_batch_id"] for row in rows] == [
        demo_batch.id,
        real_batch.id,
        real_evaluation.id,
    ]
    assert [row["expected_count"] for row in rows] == [3, 3, 3]
    assert rows[-1]["completed_count"] == 3
    assert rows[-1]["pending_count"] == 0
    with session_factory() as session:
        demo_outcomes = session.scalars(
            select(CandidateOutcome).where(
                CandidateOutcome.candidate_result_id == demo_candidate.id
            )
        ).all()
        real_outcomes = session.scalars(
            select(CandidateOutcome).where(
                CandidateOutcome.candidate_result_id == real_candidate.id,
                CandidateOutcome.evaluation_batch_id == real_evaluation.id,
            )
        ).all()
        assert [item.status for item in demo_outcomes] == [
            "PENDING",
            "PENDING",
            "PENDING",
        ]
        assert [item.status for item in real_outcomes] == [
            "COMPLETED",
            "COMPLETED",
            "COMPLETED",
        ]


def test_single_batch_failure_is_redacted_and_does_not_stop_later_batches(
    session_factory, monkeypatch
):
    with session_factory() as session:
        first = add_batch(session, date(2026, 8, 27))
        second = add_batch(session, date(2026, 8, 28))
        session.commit()

    original = CandidateOutcomeModule.evaluate_due_outcomes
    calls = []

    def fail_first(module, batch_id):
        calls.append(batch_id)
        if batch_id == first.id:
            raise RuntimeError(
                "cannot use sqlite+pysqlite:///private/secret.db\n"
                "[SQL: SELECT private_note FROM decision_note]\nTraceback secret"
            )
        return original(module, batch_id)

    monkeypatch.setattr(CandidateOutcomeModule, "evaluate_due_outcomes", fail_first)
    output = StringIO()
    error_output = StringIO()

    exit_code = run_backfill(
        session_factory,
        dry_run=False,
        calculation_version="outcome-v1",
        output=output,
        error_output=error_output,
    )

    assert exit_code == 1
    assert calls == [first.id, second.id]
    assert [json.loads(line)["evaluation_batch_id"] for line in output.getvalue().splitlines()] == [
        second.id
    ]
    failure = json.loads(error_output.getvalue())
    assert failure["evaluation_batch_id"] == first.id
    assert failure["error_type"] == "RuntimeError"
    assert "secret.db" not in error_output.getvalue()
    assert "SELECT" not in error_output.getvalue()
    assert "Traceback" not in error_output.getvalue()


@pytest.mark.parametrize("dry_run", [True, False])
def test_empty_database_is_safe(session_factory, dry_run):
    output = StringIO()
    assert run_backfill(
        session_factory,
        dry_run=dry_run,
        calculation_version="outcome-v1",
        output=output,
        error_output=StringIO(),
    ) == 0
    with session_factory() as session:
        assert session.scalar(select(func.count(OutcomeRun.id))) == 0
        assert session.scalar(select(func.count(CandidateOutcome.id))) == 0


def test_cli_passes_explicit_parameters_without_opening_a_database(monkeypatch):
    sentinel_factory = object()
    observed = {}

    def fake_factory(url):
        observed["url"] = url
        return sentinel_factory

    def fake_run(factory, **kwargs):
        observed["factory"] = factory
        observed.update(kwargs)
        return 7

    monkeypatch.setattr(backfill_script, "create_sqlite_session_factory", fake_factory)
    monkeypatch.setattr(backfill_script, "run_backfill", fake_run)

    assert main(
        [
            "--dry-run",
            "--database-url",
            "sqlite+pysqlite:///temporary.db",
            "--calculation-version",
            "outcome-v2",
        ]
    ) == 7
    assert observed["url"] == "sqlite+pysqlite:///temporary.db"
    assert observed["factory"] is sentinel_factory
    assert observed["dry_run"] is True
    assert observed["calculation_version"] == "outcome-v2"


def assert_safe_cli_failure(captured, *, phase, error_types, secrets):
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    payload = json.loads(captured.err)
    assert payload == {
        "error_type": payload["error_type"],
        "event": "candidate_outcome_backfill_failed",
        "message": "回填初始化失败" if phase == "initialization" else "回填计划读取失败",
        "phase": phase,
    }
    assert payload["error_type"] in error_types
    for secret in (*secrets, "Traceback", "SELECT", "[SQL:"):
        assert secret not in captured.err


def test_cli_invalid_database_url_returns_safe_initialization_error(capsys):
    database_url = "unknown-driver:///private/secret.db?token=classified"

    exit_code = main(["--dry-run", "--database-url", database_url])

    assert exit_code == 2
    assert_safe_cli_failure(
        capsys.readouterr(),
        phase="initialization",
        error_types={"NoSuchModuleError", "ArgumentError"},
        secrets=(database_url, "/private/secret.db", "classified"),
    )


def test_cli_unmigrated_database_returns_safe_planning_error(tmp_path, capsys):
    database_path = tmp_path / "private-unmigrated.db"
    database_url = f"sqlite+pysqlite:///{database_path}"

    exit_code = main(["--dry-run", "--database-url", database_url])

    assert exit_code == 2
    assert_safe_cli_failure(
        capsys.readouterr(),
        phase="planning",
        error_types={"OperationalError"},
        secrets=(database_url, str(database_path), "private-unmigrated.db"),
    )


def test_cli_connection_failure_returns_safe_planning_error(monkeypatch, capsys):
    class FailingFactory:
        def __call__(self):
            raise ConnectionError("/private/secret.db connection refused")

    monkeypatch.setattr(
        backfill_script,
        "create_sqlite_session_factory",
        lambda _url: FailingFactory(),
    )

    exit_code = main(
        ["--dry-run", "--database-url", "sqlite+pysqlite:///ignored-secret.db"]
    )

    assert exit_code == 2
    assert_safe_cli_failure(
        capsys.readouterr(),
        phase="planning",
        error_types={"ConnectionError"},
        secrets=("/private/secret.db", "ignored-secret.db", "connection refused"),
    )


@pytest.mark.parametrize("calculation_version", ["", "   ", "x" * 33])
def test_cli_rejects_invalid_calculation_version_with_argparse_usage(
    calculation_version, capsys
):
    with pytest.raises(SystemExit) as error:
        main(["--dry-run", "--calculation-version", calculation_version])

    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err
    assert "calculation-version" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("calculation_version", ["", "   ", "x" * 33])
def test_run_backfill_rejects_invalid_version_before_database_access(
    session_factory, calculation_version
):
    with session_factory() as session:
        batch = add_batch(session, date(2026, 8, 27))
        add_candidate(session, batch, "600001")
        session.commit()
        before = (
            session.scalar(select(func.count(DataBatch.id))),
            session.scalar(select(func.count(CandidateResult.id))),
            session.scalar(select(func.count(CandidateOutcome.id))),
            session.scalar(select(func.count(OutcomeRun.id))),
        )

    statements = []

    def capture_statements(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = session_factory.kw["bind"]
    output = StringIO()
    error_output = StringIO()
    event.listen(engine, "before_cursor_execute", capture_statements)
    try:
        with pytest.raises(ValueError, match="calculation_version"):
            run_backfill(
                session_factory,
                dry_run=True,
                calculation_version=calculation_version,
                output=output,
                error_output=error_output,
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statements)

    assert statements == []
    assert output.getvalue() == ""
    assert error_output.getvalue() == ""
    with session_factory() as session:
        after = (
            session.scalar(select(func.count(DataBatch.id))),
            session.scalar(select(func.count(CandidateResult.id))),
            session.scalar(select(func.count(CandidateOutcome.id))),
            session.scalar(select(func.count(OutcomeRun.id))),
        )
    assert after == before


def test_run_backfill_preserves_valid_32_character_version(session_factory):
    calculation_version = "v" * 32
    with session_factory() as session:
        batch = add_batch(session, date(2026, 8, 27))
        candidate = add_candidate(session, batch, "600001")
        session.add(
            CandidateOutcome(
                candidate_result_id=candidate.id,
                source_batch_id=batch.id,
                source_trade_date=batch.trade_date,
                rule_version=batch.rule_version,
                horizon_trading_days=1,
                status="COMPLETED",
                calculation_version=calculation_version,
            )
        )
        session.commit()

    output = StringIO()
    assert run_backfill(
        session_factory,
        dry_run=True,
        calculation_version=calculation_version,
        output=output,
        error_output=StringIO(),
    ) == 0

    assert json.loads(output.getvalue())["current_published_status_counts"] == {
        "COMPLETED": 0,
        "PENDING": 0,
        "UNAVAILABLE": 0,
    }
