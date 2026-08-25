from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.sqlalchemy_repositories import SQLAlchemyBatchStore, SQLAlchemySignalStore
from app.domain.indicators import IndicatorEngine
from app.domain.market import MarketBar
from app.domain.signals import SignalEngine, SignalEvaluation
from app.infrastructure.models import (
    CandidateResult,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    StockBasic,
    SyncJob,
)
from app.ports.market_data import (
    MarketDataGateway,
    MarketDataUnavailable,
    PriceRecord,
    StockRecord,
)


class NonTradingDayError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SyncResult:
    job_id: int
    batch_id: int


class SyncPipeline:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: MarketDataGateway,
        *,
        rule_version: str = "v1",
        minimum_completeness: float = 0.99,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.rule_version = rule_version
        self.minimum_completeness = minimum_completeness
        self.indicators = IndicatorEngine()
        self.signals = SignalEngine()

    def run(self, target_trade_date: date) -> SyncResult:
        with self.session_factory() as session:
            existing = session.scalar(
                select(DataBatch).where(
                    DataBatch.trade_date == target_trade_date,
                    DataBatch.status == "READY",
                    DataBatch.rule_version == self.rule_version,
                )
            )
            if existing is not None:
                job_id = session.scalar(
                    select(SyncJob.id)
                    .where(
                        SyncJob.target_trade_date == target_trade_date,
                        SyncJob.status == "READY",
                    )
                    .order_by(SyncJob.id.desc())
                )
                assert job_id is not None
                return SyncResult(job_id=job_id, batch_id=existing.id)

            job = SyncJob(
                job_type="MANUAL",
                target_trade_date=target_trade_date,
                status="PENDING",
                stage="PENDING",
                started_at=datetime.now(UTC),
            )
            session.add(job)
            session.commit()

            if not self.gateway.is_trade_date(target_trade_date):
                self._fail_job(job, "非交易日不创建数据批次")
                session.commit()
                raise NonTradingDayError(f"{target_trade_date} is not a trading day")

            batch = DataBatch(
                trade_date=target_trade_date,
                status="BUILDING",
                completeness_rate=0.0,
                rule_version=self.rule_version,
                is_active=False,
            )
            session.add(batch)
            job.status = job.stage = "FETCHING"
            session.commit()

            stocks = self.gateway.list_stocks()
            successful: list[tuple[StockBasic, list[PriceRecord]]] = []
            failures: list[str] = []
            for source_stock in stocks:
                stock = self._upsert_stock(session, source_stock)
                try:
                    records = self.gateway.daily_prices(source_stock, target_trade_date)
                except MarketDataUnavailable:
                    failures.append(source_stock.stock_code)
                    continue
                try:
                    self._validate(records)
                except ValueError:
                    failures.append(source_stock.stock_code)
                    continue
                successful.append((stock, records))

            total = len(stocks)
            batch.completeness_rate = len(successful) / total if total else 0.0
            job.completed_count = len(successful)
            job.failed_count = len(failures)
            job.progress = 1.0
            if batch.completeness_rate < self.minimum_completeness:
                batch.status = "FAILED"
                self._fail_job(job, f"数据完整率 {batch.completeness_rate:.2%} 低于阈值")
                session.commit()
                return SyncResult(job_id=job.id, batch_id=batch.id)

            job.status = job.stage = "VALIDATING"
            session.flush()
            for _, records in successful:
                self._persist_prices(session, batch.id, records)

            job.status = job.stage = "CALCULATING"
            for stock, records in successful:
                self._calculate_stock(session, batch, stock, records)

            job.status = job.stage = "GENERATING_SIGNALS"
            batch.status = "READY"
            SQLAlchemyBatchStore(session).activate_ready_batch(batch.id)
            job.status = job.stage = "READY"
            job.finished_at = datetime.now(UTC)
            session.commit()
            return SyncResult(job_id=job.id, batch_id=batch.id)

    @staticmethod
    def _fail_job(job: SyncJob, message: str) -> None:
        job.status = "FAILED"
        job.error_summary = message
        job.error_message = message
        job.finished_at = datetime.now(UTC)

    @staticmethod
    def _upsert_stock(session: Session, source: StockRecord) -> StockBasic:
        market = source.market
        code = source.stock_code
        stock = session.scalar(
            select(StockBasic).where(StockBasic.market == market, StockBasic.stock_code == code)
        )
        if stock is None:
            stock = StockBasic(market=market, stock_code=code, stock_name=source.name)
            session.add(stock)
        stock.industry = source.industry
        stock.list_date = source.list_date
        stock.is_st = source.is_st
        return stock

    @staticmethod
    def _validate(records: list[PriceRecord]) -> None:
        if not records:
            raise ValueError("empty price history")
        for item in records:
            if min(item.open, item.high, item.low, item.close, item.volume, item.amount) < 0:
                raise ValueError("price values cannot be negative")
            if item.high < max(item.open, item.close) or item.low > min(item.open, item.close):
                raise ValueError("invalid OHLC")

    @staticmethod
    def _persist_prices(session: Session, batch_id: int, records: list[PriceRecord]) -> None:
        session.add_all(
            DailyPrice(
                batch_id=batch_id,
                market=item.market,
                stock_code=item.stock_code,
                trade_date=item.trade_date,
                adjustment=item.adjustment,
                open=item.open,
                high=item.high,
                low=item.low,
                close=item.close,
                volume=item.volume,
                amount=item.amount,
                pct_change=item.pct_change,
                turnover_rate=item.turnover_rate,
                is_suspended=item.is_suspended,
            )
            for item in records
        )

    def _calculate_stock(
        self,
        session: Session,
        batch: DataBatch,
        stock: StockBasic,
        records: list[PriceRecord],
    ) -> None:
        qfq = [item for item in records if item.adjustment == "qfq"]
        bars = [
            MarketBar(
                trade_date=item.trade_date,
                close_qfq=item.close,
                volume=item.volume,
                high_qfq=item.high,
                pct_change_raw=item.pct_change,
            )
            for item in qfq
        ]
        snapshots = self.indicators.calculate(bars)
        evaluations = self.signals.evaluate(bars, snapshots, self.rule_version)
        for snapshot in snapshots:
            values = asdict(snapshot)
            values.pop("trade_date")
            values["unavailable"] = sorted(values["unavailable"])
            session.add(
                DailyIndicator(
                    batch_id=batch.id,
                    market=stock.market,
                    stock_code=stock.stock_code,
                    trade_date=snapshot.trade_date,
                    rule_version=self.rule_version,
                    values=values,
                )
            )
        signal_store = SQLAlchemySignalStore(session)
        for evaluation in evaluations:
            for rule_code in sorted(evaluation.event_codes):
                signal_store.record_signal(
                    batch_id=batch.id,
                    market=stock.market,
                    stock_code=stock.stock_code,
                    trade_date=evaluation.trade_date,
                    rule_code=rule_code,
                    rule_version=self.rule_version,
                    payload={"risk_level": evaluation.risk_level, "trend": evaluation.trend},
                )
        if evaluations and self._is_candidate(evaluations):
            current = evaluations[-1]
            session.add(
                CandidateResult(
                    batch_id=batch.id,
                    market=stock.market,
                    stock_code=stock.stock_code,
                    score=float(len(current.state_codes) + len(current.event_codes)),
                    reasons=sorted(current.state_codes | current.event_codes),
                )
            )

    @staticmethod
    def _is_candidate(evaluations: list[SignalEvaluation]) -> bool:
        current = evaluations[-1]
        required = {"PRICE_ABOVE_MA20", "MA5_ABOVE_MA20", "RSI_STRONG"}
        recent_events = set().union(*(item.event_codes for item in evaluations[-3:]))
        return required <= current.state_codes and bool(
            recent_events & {"BREAKOUT_MA20_WITH_VOLUME", "MACD_GOLDEN_CROSS"}
        )
