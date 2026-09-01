from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.database import (
    create_sqlite_memory_session_factory,
    create_sqlite_session_factory,
)
from app.infrastructure.models import Base, CandidateOutcome, DailyPrice, DataBatch, OutcomeRun


def test_memory_session_factory_rejects_orphan_outcome_records() -> None:
    factory = create_sqlite_memory_session_factory()
    Base.metadata.create_all(factory.kw["bind"])

    with factory() as session:
        session.add(
            CandidateOutcome(
                candidate_result_id=999,
                source_batch_id=998,
                source_trade_date=date(2026, 8, 25),
                rule_version="rule-v1",
                horizon_trading_days=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(OutcomeRun(evaluation_batch_id=997, rule_version="rules-v1"))
        with pytest.raises(IntegrityError):
            session.commit()

    factory.kw["bind"].dispose()


@pytest.mark.parametrize("storage", ["memory", "file"])
def test_sqlite_session_factories_enable_foreign_keys(tmp_path: Path, storage: str) -> None:
    if storage == "memory":
        factory = create_sqlite_memory_session_factory()
    else:
        database_path = tmp_path / "foreign-keys.db"
        factory = create_sqlite_session_factory(f"sqlite+pysqlite:///{database_path}")

    with factory() as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    factory.kw["bind"].dispose()


def test_alembic_upgrade_creates_core_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {
        "alembic_version",
        "data_batch",
        "daily_price",
        "daily_indicator",
        "signal_event",
        "analysis_report",
        "watchlist_item",
        "operation_log",
        "realtime_refresh",
        "realtime_snapshot",
        "candidate_outcome",
        "outcome_run",
    } <= tables


def test_candidate_outcome_migration_has_required_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "candidate-outcomes.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    inspector = inspect(engine)
    outcome_columns = {
        column["name"]: column for column in inspector.get_columns("candidate_outcome")
    }
    run_columns = {column["name"]: column for column in inspector.get_columns("outcome_run")}

    expected_outcome_nullability = {
        "id": False,
        "candidate_result_id": False,
        "source_batch_id": False,
        "evaluation_batch_id": True,
        "outcome_run_id": True,
        "source_trade_date": False,
        "rule_version": False,
        "horizon_trading_days": False,
        "reference_trade_date": True,
        "evaluation_trade_date": True,
        "expected_evaluation_trade_date": True,
        "reference_price": True,
        "evaluation_price": True,
        "return_rate": True,
        "mfe": True,
        "mae": True,
        "status": False,
        "unavailable_reason": True,
        "calculation_version": False,
        "created_at": False,
        "updated_at": False,
    }
    assert {
        name: column["nullable"] for name, column in outcome_columns.items()
    } == expected_outcome_nullability

    outcome_foreign_keys = {
        (tuple(foreign_key["constrained_columns"]), foreign_key["referred_table"])
        for foreign_key in inspector.get_foreign_keys("candidate_outcome")
    }
    assert (("candidate_result_id",), "candidate_result") in outcome_foreign_keys
    assert (("source_batch_id",), "data_batch") in outcome_foreign_keys
    assert (("evaluation_batch_id",), "data_batch") in outcome_foreign_keys
    assert (("outcome_run_id",), "outcome_run") in outcome_foreign_keys
    assert (
        "candidate_result_id",
        "horizon_trading_days",
        "calculation_version",
        "outcome_run_id",
    ) in {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("candidate_outcome")
    }
    outcome_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("candidate_outcome")
    }
    assert (
        "rule_version",
        "horizon_trading_days",
        "source_trade_date",
        "status",
    ) in set(outcome_indexes.values())
    assert outcome_indexes["ix_candidate_outcome_window"] == (
        "calculation_version",
        "rule_version",
        "source_trade_date",
    )
    assert outcome_indexes["ix_candidate_outcome_snapshot"] == (
        "outcome_run_id",
        "calculation_version",
    )

    expected_run_nullability = {
        "id": False,
        "evaluation_batch_id": False,
        "calculation_version": False,
        "rule_version": False,
        "attempt_no": False,
        "status": False,
        "expected_count": False,
        "completed_count": False,
        "unavailable_count": False,
        "pending_count": False,
        "started_at": False,
        "finished_at": True,
        "error_summary": True,
    }
    assert {
        name: column["nullable"] for name, column in run_columns.items()
    } == expected_run_nullability
    run_foreign_keys = {
        (tuple(foreign_key["constrained_columns"]), foreign_key["referred_table"])
        for foreign_key in inspector.get_foreign_keys("outcome_run")
    }
    assert (("evaluation_batch_id",), "data_batch") in run_foreign_keys
    assert (
        "evaluation_batch_id",
        "calculation_version",
        "rule_version",
        "attempt_no",
    ) in {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("outcome_run")
    }
    engine.dispose()


def test_candidate_outcome_migration_enforces_idempotency_and_stores_results(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "candidate-outcome-values.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO data_batch "
                "(id,source,trade_date,status,completeness_rate,rule_version,is_active,created_at) "
                "VALUES "
                "(1,'test','2026-08-25','READY',1,'rule-v1',0,CURRENT_TIMESTAMP),"
                "(2,'test','2026-08-28','READY',1,'rule-v1',0,CURRENT_TIMESTAMP),"
                "(3,'test','2026-08-29','READY',1,'rule-v1',0,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO candidate_result "
                "(id,batch_id,market,stock_code,score,reasons,positive_event_count) "
                "VALUES (10,1,'SH','600000',88,'[]',2)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO outcome_run "
                "(id,evaluation_batch_id,calculation_version,rule_version,attempt_no,status) "
                "VALUES (20,2,'outcome-v1','rule-v1',1,'COMPLETED'),"
                "(21,3,'outcome-v1','rule-v1',1,'COMPLETED')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO candidate_outcome "
                "(candidate_result_id,source_batch_id,evaluation_batch_id,outcome_run_id,source_trade_date,"
                "rule_version,horizon_trading_days,reference_trade_date,evaluation_trade_date,"
                "reference_price,evaluation_price,return_rate,mfe,mae,status) "
                "VALUES (10,1,2,20,'2026-08-25','rule-v1',1,'2026-08-25','2026-08-26',"
                "10,11,0.1,0.15,-0.03,'COMPLETED')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO candidate_outcome "
                "(candidate_result_id,source_batch_id,source_trade_date,rule_version,"
                "horizon_trading_days,status,unavailable_reason) "
                "VALUES (10,1,'2026-08-25','rule-v1',3,'UNAVAILABLE','MISSING_PRICE')"
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO candidate_outcome "
                "(candidate_result_id,source_batch_id,evaluation_batch_id,outcome_run_id,source_trade_date,"
                "rule_version,horizon_trading_days) "
                "VALUES (10,1,2,20,'2026-08-25','rule-v1',1)"
            )
        )

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO candidate_outcome "
                "(candidate_result_id,source_batch_id,evaluation_batch_id,outcome_run_id,source_trade_date,"
                "rule_version,horizon_trading_days) "
                "VALUES (10,1,3,21,'2026-08-25','rule-v1',1)"
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO outcome_run "
                "(evaluation_batch_id,rule_version,attempt_no) "
                "VALUES (2,'rule-v1',1)"
            )
        )

    with engine.connect() as connection:
        completed = connection.execute(
            text(
                "SELECT reference_price,evaluation_price,return_rate,mfe,mae "
                "FROM candidate_outcome WHERE status='COMPLETED'"
            )
        ).one()
        unavailable = connection.execute(
            text(
                "SELECT unavailable_reason,reference_price,evaluation_price,return_rate,mfe,mae "
                "FROM candidate_outcome WHERE status='UNAVAILABLE'"
            )
        ).one()

    assert completed == (10.0, 11.0, 0.1, 0.15, -0.03)
    assert unavailable == ("MISSING_PRICE", None, None, None, None, None)
    engine.dispose()


def test_candidate_outcome_migration_downgrades_and_upgrades_cleanly(tmp_path: Path) -> None:
    database_path = tmp_path / "candidate-outcome-roundtrip.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")
    command.upgrade(config, "head")

    command.downgrade(config, "a95e073bf254")
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    downgraded_tables = set(inspect(engine).get_table_names())
    assert "candidate_outcome" not in downgraded_tables
    assert "outcome_run" not in downgraded_tables
    assert {"realtime_refresh", "realtime_snapshot"} <= downgraded_tables

    command.upgrade(config, "head")
    upgraded_tables = set(inspect(engine).get_table_names())
    assert {"candidate_outcome", "outcome_run"} <= upgraded_tables
    engine.dispose()


def test_watchlist_scope_migration_preserves_existing_market_jobs_and_snapshot(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'watchlist-scope.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "f84d962ae143")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO realtime_refresh "
                "(id,status,stage,total_count,completed_count,failed_count,started_at) "
                "VALUES (7,'READY','READY',5550,5550,0,CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text("INSERT INTO realtime_snapshot (id,summary,quotes) VALUES (1,'{}','[]')")
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT id,status,scope,requested_symbols FROM realtime_refresh")
        ).one() == (7, "READY", "market", "[]")
        assert connection.execute(
            text("SELECT id,summary,quotes FROM realtime_snapshot")
        ).one() == (1, "{}", "[]")
    engine.dispose()


def test_cdr_volume_migration_only_corrects_legacy_tencent_689_rows(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'cdr.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "d62b740ec921")
    engine = create_engine(url)
    with Session(engine) as session:
        for source in ("tencent-sina-v1", "demo-v1"):
            batch = DataBatch(source=source, trade_date=date(2026, 8, 27), rule_version="v1")
            session.add(batch)
            session.flush()
            for code in ("689009", "688001"):
                session.add(
                    DailyPrice(
                        batch_id=batch.id,
                        market="SH",
                        stock_code=code,
                        trade_date=batch.trade_date,
                        adjustment="raw",
                        open=10,
                        high=11,
                        low=9,
                        close=10,
                        volume=917474600,
                        amount=399360000,
                    )
                )
        session.commit()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        values = connection.execute(
            text(
                "SELECT b.source,p.stock_code,p.volume,p.amount "
                "FROM daily_price p JOIN data_batch b ON b.id=p.batch_id"
            )
        ).all()
        for source, code, volume, amount in values:
            assert volume == (
                9174746 if source == "tencent-sina-v1" and code == "689009" else 917474600
            )
            assert amount == 399360000
    engine.dispose()


def test_signal_migration_preserves_existing_ids_payload_and_confirmation(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'existing.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "c52a98631dea")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO data_batch "
                "(id,trade_date,status,completeness_rate,rule_version,is_active,source,created_at) "
                "VALUES (1,'2026-08-26','READY',1,'v1',1,'real',CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO signal_event "
                "(id,batch_id,market,stock_code,trade_date,rule_code,rule_version,payload) "
                "VALUES (7,1,'SH','600000','2026-08-26','TEST','v1','{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alert_event_state (id,signal_event_id,status) VALUES (9,7,'CONFIRMED')"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id,batch_id,payload FROM signal_event")).one() == (
            7,
            1,
            "{}",
        )
        assert connection.execute(
            text("SELECT signal_event_id,status FROM alert_event_state")
        ).one() == (7, "CONFIRMED")
        constraints = inspect(connection).get_unique_constraints("signal_event")
        assert constraints[0]["column_names"][0] == "batch_id"
    engine.dispose()
