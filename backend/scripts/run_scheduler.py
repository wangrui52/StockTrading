import time
from datetime import UTC, datetime

from app.adapters.akshare_market_data import AkShareMarketDataGateway
from app.application.scheduler import DailySyncScheduler
from app.application.sync_pipeline import SyncPipeline
from app.infrastructure.database import create_sqlite_session_factory


def main() -> None:
    factory = create_sqlite_session_factory()
    gateway = AkShareMarketDataGateway()
    pipeline = SyncPipeline(factory, gateway)
    scheduler = DailySyncScheduler(factory, gateway.is_trade_date, pipeline.run)
    while True:
        scheduler.tick(datetime.now(UTC))
        time.sleep(30)


if __name__ == "__main__":
    main()
