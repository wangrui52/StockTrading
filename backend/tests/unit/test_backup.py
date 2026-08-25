import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.application.backup import backup_database


def test_backup_uses_seven_rotating_weekday_slots(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("create table sample(value integer)")
        connection.execute("insert into sample values (42)")
    backup_dir = tmp_path / "backups"
    start = datetime(2025, 3, 31, tzinfo=UTC)

    paths = {backup_database(source, backup_dir, start + timedelta(days=day)) for day in range(8)}

    assert len(paths) == 7
    assert len(list(backup_dir.glob("*.db"))) == 7
    with sqlite3.connect(backup_dir / "stock-trading-weekday-0.db") as connection:
        assert connection.execute("select value from sample").fetchone() == (42,)
