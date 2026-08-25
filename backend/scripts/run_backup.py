import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.application.backup import backup_database


def main() -> None:
    source = Path(os.getenv("DATABASE_PATH", "/data/stock_trading.db"))
    backup_dir = Path(os.getenv("BACKUP_DIR", "/data/backups"))
    last_date = None
    while True:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        if now.hour >= 2 and last_date != now.date():
            backup_database(source, backup_dir, now)
            last_date = now.date()
        time.sleep(60)


if __name__ == "__main__":
    main()
