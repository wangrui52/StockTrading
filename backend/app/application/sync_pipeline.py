from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.sqlalchemy_repositories import SQLAlchemyBatchStore, SQLAlchemySignalStore
from app.domain.candidates import CandidateEngine
from app.domain.indicators import IndicatorEngine
from app.domain.market import MarketBar
from app.domain.signals import SignalEngine
from app.infrastructure.models import (
    AlertRuleVersion,
    CandidateResult,
    DailyIndicator,
    DailyPrice,
    DataBatch,
    IndexDaily,
    StockBasic,
    SyncJob,
    TradeCalendar,
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
        self.candidates = CandidateEngine()

    def run(self, target_trade_date: date, *, job_type: str = "MANUAL") -> SyncResult:
        result, should_execute = self.prepare(target_trade_date, job_type=job_type)
        if should_execute:
            self.execute_prepared(result.job_id, result.batch_id, target_trade_date)
        return result

    def prepare(
        self, target_trade_date: date, *, job_type: str = "MANUAL"
    ) -> tuple[SyncResult, bool]:
        with self.session_factory() as session:
            running = session.scalar(
                select(SyncJob)
                .where(
                    SyncJob.target_trade_date == target_trade_date,
                    SyncJob.status.in_(
                        ("PENDING", "FETCHING", "VALIDATING", "CALCULATING", "GENERATING_SIGNALS")
                    ),
                )
                .order_by(SyncJob.id.desc())
            )
            if running is not None and running.batch_id is not None:
                return SyncResult(job_id=running.id, batch_id=running.batch_id), False
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
                return SyncResult(job_id=job_id, batch_id=existing.id), False

            job = SyncJob(
                job_type=job_type,
                target_trade_date=target_trade_date,
                status="PENDING",
                stage="PENDING",
                started_at=datetime.now(UTC),
            )
            session.add(job)
            session.commit()

            is_open = self.gateway.is_trade_date(target_trade_date)
            calendar = session.scalar(
                select(TradeCalendar).where(
                    TradeCalendar.market == "CN",
                    TradeCalendar.trade_date == target_trade_date,
                )
            )
            if calendar is None:
                session.add(
                    TradeCalendar(market="CN", trade_date=target_trade_date, is_open=is_open)
                )
            else:
                calendar.is_open = is_open
            session.commit()
            if not is_open:
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
            session.flush()
            job.batch_id = batch.id
            job.status = job.stage = "FETCHING"
            session.commit()
            return SyncResult(job_id=job.id, batch_id=batch.id), True

    def execute_prepared(self, job_id: int, batch_id: int, target_trade_date: date) -> SyncResult:
        with self.session_factory() as session:
            job = session.get(SyncJob, job_id)
            batch = session.get(DataBatch, batch_id)
            if job is None or batch is None:
                raise ValueError("prepared sync job or batch does not exist")
            try:
                return self._build_batch(session, job, batch, target_trade_date)
            except Exception as error:
                session.rollback()
                failed_job = session.get(SyncJob, job.id)
                failed_batch = session.get(DataBatch, batch.id)
                assert failed_job is not None and failed_batch is not None
                failed_batch.status = "FAILED"
                self._fail_job(
                    failed_job,
                    f"{failed_job.stage}: {type(error).__name__}: {str(error)[:300]}",
                )
                session.commit()
                raise

    def _build_batch(
        self,
        session: Session,
        job: SyncJob,
        batch: DataBatch,
        target_trade_date: date,
    ) -> SyncResult:
        stocks = self.gateway.list_stocks()
        previous_batch = session.scalar(
            select(DataBatch).where(
                DataBatch.is_active.is_(True),
                DataBatch.id != batch.id,
            )
        )
        incremental_start = target_trade_date - timedelta(days=10) if previous_batch else None
        successful: list[tuple[StockBasic, list[PriceRecord]]] = []
        failures: list[str] = []
        total = len(stocks)
        for position, source_stock in enumerate(stocks, start=1):
            stock = self._upsert_stock(session, source_stock)
            records = None
            for attempt in range(3):
                try:
                    fetched = self.gateway.daily_prices(
                        source_stock,
                        target_trade_date,
                        start_date=incremental_start,
                    )
                    if previous_batch and self._has_qfq_revision(
                        session, previous_batch, source_stock, fetched
                    ):
                        # 前复权价格在公司行为后会整体回溯变化；一旦重叠窗口发现修订，
                        # 扩大回拉范围，确保至少覆盖最近 250 个有效交易日。
                        fetched = self.gateway.daily_prices(
                            source_stock,
                            target_trade_date,
                            start_date=target_trade_date - timedelta(days=550),
                        )
                    records = self._merge_with_previous(
                        session,
                        previous_batch,
                        source_stock,
                        fetched,
                        target_trade_date,
                    )
                    break
                except MarketDataUnavailable:
                    if attempt < 2:
                        job.retry_count += 1
            if records is None:
                failures.append(source_stock.stock_code)
            else:
                try:
                    self._validate(records)
                except ValueError:
                    failures.append(source_stock.stock_code)
                else:
                    successful.append((stock, records))
            job.completed_count = len(successful)
            job.failed_count = len(failures)
            job.failed_items = list(failures)
            job.progress = position / total * 0.5 if total else 0.5
            if position % 50 == 0:
                session.commit()

        batch.completeness_rate = len(successful) / total if total else 0.0
        job.completed_count = len(successful)
        job.failed_count = len(failures)
        job.failed_items = list(failures)
        job.progress = 1.0
        if batch.completeness_rate < self.minimum_completeness:
            batch.status = "FAILED"
            self._fail_job(job, f"数据完整率 {batch.completeness_rate:.2%} 低于阈值")
            session.commit()
            return SyncResult(job_id=job.id, batch_id=batch.id)

        job.status = job.stage = "VALIDATING"
        session.commit()
        for _, records in successful:
            self._persist_prices(session, batch.id, records)
        try:
            for item in self.gateway.index_prices(target_trade_date):
                session.add(
                    IndexDaily(
                        batch_id=batch.id,
                        index_code=item.index_code,
                        trade_date=item.trade_date,
                        open=item.open,
                        high=item.high,
                        low=item.low,
                        close=item.close,
                        pct_change=item.pct_change,
                    )
                )
        except MarketDataUnavailable:
            pass

        latest_custom_rules: dict[int, AlertRuleVersion] = {}
        for item in session.scalars(
            select(AlertRuleVersion).order_by(AlertRuleVersion.logical_id, AlertRuleVersion.version)
        ):
            latest_custom_rules[item.logical_id] = item
        custom_rules = [item for item in latest_custom_rules.values() if item.enabled]

        job.status = job.stage = "CALCULATING"
        session.commit()
        for stock, records in successful:
            self._calculate_stock(session, batch, stock, records, custom_rules)

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
    def _merge_with_previous(
        session: Session,
        previous_batch: DataBatch | None,
        stock: StockRecord,
        fetched: list[PriceRecord],
        target_trade_date: date,
    ) -> list[PriceRecord]:
        previous: list[PriceRecord] = []
        if previous_batch is not None:
            previous = [
                PriceRecord(
                    market=item.market,
                    stock_code=item.stock_code,
                    trade_date=item.trade_date,
                    open=item.open,
                    high=item.high,
                    low=item.low,
                    close=item.close,
                    volume=item.volume,
                    amount=item.amount,
                    pct_change=item.pct_change,
                    turnover_rate=item.turnover_rate,
                    adjustment=item.adjustment,
                    is_suspended=item.is_suspended,
                )
                for item in session.scalars(
                    select(DailyPrice).where(
                        DailyPrice.batch_id == previous_batch.id,
                        DailyPrice.market == stock.market,
                        DailyPrice.stock_code == stock.stock_code,
                    )
                )
            ]
        merged = {(item.trade_date, item.adjustment): item for item in previous}
        merged.update({(item.trade_date, item.adjustment): item for item in fetched})
        if fetched and max(item.trade_date for item in fetched) < target_trade_date:
            for adjustment in ("raw", "qfq"):
                latest = max(
                    (item for item in merged.values() if item.adjustment == adjustment),
                    key=lambda item: item.trade_date,
                    default=None,
                )
                if latest is not None:
                    merged[(target_trade_date, adjustment)] = PriceRecord(
                        market=latest.market,
                        stock_code=latest.stock_code,
                        trade_date=target_trade_date,
                        open=latest.close,
                        high=latest.close,
                        low=latest.close,
                        close=latest.close,
                        volume=0,
                        amount=0,
                        pct_change=None,
                        turnover_rate=None,
                        adjustment=adjustment,
                        is_suspended=True,
                    )
        order = {"raw": 0, "qfq": 1}
        return sorted(merged.values(), key=lambda item: (item.trade_date, order[item.adjustment]))

    @staticmethod
    def _has_qfq_revision(
        session: Session,
        previous_batch: DataBatch,
        stock: StockRecord,
        fetched: list[PriceRecord],
    ) -> bool:
        fetched_qfq = {
            item.trade_date: item
            for item in fetched
            if item.adjustment == "qfq"
        }
        if not fetched_qfq:
            return False
        previous = {
            item.trade_date: item
            for item in session.scalars(
                select(DailyPrice).where(
                    DailyPrice.batch_id == previous_batch.id,
                    DailyPrice.market == stock.market,
                    DailyPrice.stock_code == stock.stock_code,
                    DailyPrice.adjustment == "qfq",
                    DailyPrice.trade_date.in_(fetched_qfq),
                )
            )
        }
        for trade_date, current in fetched_qfq.items():
            prior = previous.get(trade_date)
            if prior is not None and any(
                abs(left - right) > 1e-8
                for left, right in (
                    (prior.open, current.open),
                    (prior.high, current.high),
                    (prior.low, current.low),
                    (prior.close, current.close),
                )
            ):
                return True
        return False

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
        custom_rules: list[AlertRuleVersion],
    ) -> None:
        qfq = [item for item in records if item.adjustment == "qfq"]
        bars = [
            MarketBar(
                trade_date=item.trade_date,
                close_qfq=item.close,
                volume=item.volume,
                high_qfq=item.high,
                pct_change_raw=item.pct_change,
                is_suspended=item.is_suspended,
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
        if snapshots and evaluations:
            latest = snapshots[-1]
            latest_bar = bars[-1]
            for rule in custom_rules:
                if self._custom_rule_matches(rule, latest, latest_bar):
                    signal_store.record_signal(
                        batch_id=batch.id,
                        market=stock.market,
                        stock_code=stock.stock_code,
                        trade_date=latest.trade_date,
                        rule_code=rule.rule_code,
                        rule_version=self.rule_version,
                        payload={
                            "risk_level": evaluations[-1].risk_level,
                            "trend": evaluations[-1].trend,
                            "custom_rule_logical_id": rule.logical_id,
                            "custom_rule_version": rule.version,
                            "threshold": rule.threshold,
                        },
                    )
        decision = self.candidates.evaluate(
            is_st=stock.is_st,
            bars=bars,
            indicators=snapshots,
            evaluations=evaluations,
        )
        if decision.matched:
            session.add(
                CandidateResult(
                    batch_id=batch.id,
                    market=stock.market,
                    stock_code=stock.stock_code,
                    score=decision.score,
                    reasons=sorted(decision.reasons),
                    positive_event_count=len(
                        decision.reasons & {"BREAKOUT_MA20_WITH_VOLUME", "MACD_GOLDEN_CROSS"}
                    ),
                    volume_ratio=snapshots[-1].volume_ratio_5_20,
                    pct_change=bars[-1].pct_change_raw,
                )
            )

    @staticmethod
    def _custom_rule_matches(rule: AlertRuleVersion, snapshot: object, bar: MarketBar) -> bool:
        if rule.rule_code == "CUSTOM_RSI":
            value = getattr(snapshot, "rsi14", None)
            return value is not None and value > rule.threshold
        if rule.rule_code == "CUSTOM_DAILY_DROP":
            return bar.pct_change_raw is not None and bar.pct_change_raw <= -abs(rule.threshold)
        if rule.rule_code == "CUSTOM_VOLUME_RATIO":
            value = getattr(snapshot, "volume_ratio_5_20", None)
            return value is not None and value >= rule.threshold
        return False
