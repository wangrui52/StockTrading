from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


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
