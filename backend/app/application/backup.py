import sqlite3
from datetime import datetime
from pathlib import Path


def backup_database(source_path: Path, backup_dir: Path, now: datetime) -> Path:
    """使用 SQLite 在线备份，并以星期槽位轮换，始终保留最近 7 份。"""
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"stock-trading-weekday-{now.weekday()}.db"
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    return destination
