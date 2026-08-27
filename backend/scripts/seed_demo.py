"""为本地 E2E 创建 100 只股票、250 个交易日的确定性样本。"""

from datetime import date, timedelta

from sqlalchemy import func, select

from app.adapters.fake_market_data import FakeMarketDataGateway
from app.application.sync_pipeline import SyncPipeline
from app.infrastructure.database import create_sqlite_session_factory
from app.infrastructure.models import CandidateResult, DataBatch, SignalEvent
from app.ports.market_data import IndexRecord, PriceRecord, StockRecord


def trading_dates(end: date, count: int) -> list[date]:
    values: list[date] = []
    current = end
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current -= timedelta(days=1)
    return list(reversed(values))


def main() -> None:
    factory = create_sqlite_session_factory()
    with factory() as session:
        existing = session.scalar(select(DataBatch).where(DataBatch.is_active.is_(True)))
        if existing is not None:
            print(f"demo batch already exists: {existing.id}")
            return

    target = date(2025, 3, 31)
    dates = trading_dates(target, 250)
    stocks: list[StockRecord] = []
    histories: dict[str, list[PriceRecord]] = {}
    for index in range(100):
        if index % 3 == 0:
            market, code = "SH", f"{600000 + index:06d}"
        elif index % 3 == 1:
            market, code = "SZ", f"{index:06d}"
        else:
            market, code = "BJ", f"{430000 + index:06d}"
        stock = StockRecord(
            market=market,
            stock_code=code,
            name=f"示例股份{index + 1:03d}",
            industry="固定样本",
            list_date=date(2010, 1, 1),
            is_st=index == 97,
        )
        stocks.append(stock)
        records: list[PriceRecord] = []
        for position, trade_date in enumerate(dates):
            close = 8 + index * 0.03 + position * 0.015
            for adjustment in ("raw", "qfq"):
                records.append(
                    PriceRecord(
                        market=market,
                        stock_code=code,
                        trade_date=trade_date,
                        open=close - 0.05,
                        high=close + 0.12,
                        low=close - 0.12,
                        close=close,
                        volume=100_000 + position * 100,
                        amount=close * (100_000 + position * 100),
                        pct_change=0.2,
                        turnover_rate=1.2,
                        adjustment=adjustment,
                    )
                )
        histories[code] = records
    indices = [
        IndexRecord(
            index_code=code,
            trade_date=target,
            open=3000 + offset,
            high=3050 + offset,
            low=2990 + offset,
            close=3040 + offset,
            pct_change=1.2,
        )
        for offset, code in enumerate(("000001", "399001", "399006", "899050"))
    ]
    result = SyncPipeline(
        factory,
        FakeMarketDataGateway(
            open_dates={target},
            stocks=stocks,
            histories=histories,
            indices=indices,
            adapter_version="demo-v1",
        ),
    ).run(target)
    with factory() as session:
        candidate_count = session.scalar(
            select(func.count(CandidateResult.id)).where(
                CandidateResult.batch_id == result.batch_id
            )
        )
        if not candidate_count:
            first = stocks[0]
            session.add(
                CandidateResult(
                    batch_id=result.batch_id,
                    market=first.market,
                    stock_code=first.stock_code,
                    score=4,
                    reasons=["PRICE_ABOVE_MA20", "MACD_GOLDEN_CROSS"],
                )
            )
            session.add(
                SignalEvent(
                    batch_id=result.batch_id,
                    market=first.market,
                    stock_code=first.stock_code,
                    trade_date=target,
                    rule_code="MACD_GOLDEN_CROSS",
                    rule_version="v1",
                    payload={"risk_level": "low", "source": "demo"},
                )
            )
            session.commit()
    print(f"seeded demo batch={result.batch_id}, stocks={len(stocks)}, days={len(dates)}")


if __name__ == "__main__":
    main()
