from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.infrastructure.models import DailyPrice, DataBatch


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
    } <= tables


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
