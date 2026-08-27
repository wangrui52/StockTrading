import time
from datetime import UTC, datetime

from app.adapters.tencent_market_data import TencentMarketDataGateway
from app.application.scheduler import DailySyncScheduler
from app.application.sync_pipeline import SyncPipeline
from app.infrastructure.database import create_sqlite_session_factory


def main() -> None:
    factory = create_sqlite_session_factory()
    gateway = TencentMarketDataGateway()
    pipeline = SyncPipeline(factory, gateway, fetch_workers=4)
    scheduler = DailySyncScheduler(factory, gateway.is_trade_date, pipeline.run)
    while True:
        scheduler.tick(datetime.now(UTC))
        time.sleep(30)


if __name__ == "__main__":
    main()
