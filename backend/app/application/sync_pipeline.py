import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import insert, select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.sqlalchemy_repositories import SQLAlchemyBatchStore, SQLAlchemySignalStore
from app.application.batch_snapshot import batch_lineage_ids, price_rows
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
    MarketBreadthSnapshot,
    StockBasic,
    SyncJob,
    TradeCalendar,
)
from app.ports.market_data import (
    MarketDataGateway,
    MarketDataUnavailable,
    PriceRecord,
    StockRecord,
    TradeCalendarRecord,
)

logger = logging.getLogger(__name__)


class NonTradingDayError(ValueError):
    pass


class SyncInProgressError(ValueError):
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
        fetch_workers: int = 1,
        outcome_runner: Callable[[int], object] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.rule_version = rule_version
        self.minimum_completeness = minimum_completeness
        self.fetch_workers = max(1, min(fetch_workers, 4))
        self.outcome_runner = outcome_runner
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
            # SQLite 先占写锁再检查并创建任务，跨 API/调度进程保持原子性。
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            running = session.scalar(
                select(SyncJob)
                .where(
                    SyncJob.status.in_(
                        ("PENDING", "FETCHING", "VALIDATING", "CALCULATING", "GENERATING_SIGNALS")
                    ),
                )
                .order_by(SyncJob.id.desc())
            )
            if running is not None:
                if running.target_trade_date == target_trade_date and running.batch_id is not None:
                    return SyncResult(job_id=running.id, batch_id=running.batch_id), False
                raise SyncInProgressError("已有同步任务正在执行，请等待完成")
            existing = session.scalar(
                select(DataBatch).where(
                    DataBatch.trade_date == target_trade_date,
                    DataBatch.status == "READY",
                    DataBatch.rule_version == self.rule_version,
                    DataBatch.source == self.gateway.adapter_version,
                )
            )
            if existing is not None:
                job_id = session.scalar(
                    select(SyncJob.id)
                    .where(
                        SyncJob.batch_id == existing.id,
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

            try:
                is_open = self.gateway.is_trade_date(target_trade_date)
            except Exception as error:
                self._fail_job(job, f"交易日历获取失败：{error}")
                session.commit()
                raise
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
                parent_batch_id=session.scalar(
                    select(DataBatch.id)
                    .where(
                        DataBatch.is_active.is_(True),
                        DataBatch.source == self.gateway.adapter_version,
                    )
                    .order_by(DataBatch.id.desc())
                ),
                source=self.gateway.adapter_version,
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
                failed_stage = job.stage
                session.rollback()
                failed_job = session.get(SyncJob, job.id)
                failed_batch = session.get(DataBatch, batch.id)
                assert failed_job is not None and failed_batch is not None
                failed_job.stage = failed_stage
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
        previous_batch = (
            session.get(DataBatch, batch.parent_batch_id) if batch.parent_batch_id else None
        )
        if previous_batch and not 0 < (target_trade_date - previous_batch.trade_date).days <= 10:
            batch.parent_batch_id = None
            previous_batch = None
        incremental_start = target_trade_date - timedelta(days=10) if previous_batch else None
        calendar_start: date | None = None
        successful_count = 0
        failures: list[str] = []
        total = len(stocks)
        latest_custom_rules: dict[int, AlertRuleVersion] = {}
        for item in session.scalars(
            select(AlertRuleVersion).order_by(AlertRuleVersion.logical_id, AlertRuleVersion.version)
        ):
            latest_custom_rules[item.logical_id] = item
        custom_rules = [item for item in latest_custom_rules.values() if item.enabled]
        known_stocks = (
            set(
                session.execute(
                    select(DailyPrice.market, DailyPrice.stock_code)
                    .where(DailyPrice.batch_id.in_(batch_lineage_ids(session, previous_batch.id)))
                    .distinct()
                ).all()
            )
            if previous_batch
            else set()
        )
        starts = {
            (s.market, s.stock_code): incremental_start
            if (s.market, s.stock_code) in known_stocks
            else None
            for s in stocks
        }
        for position, (source_stock, fetched, retries) in enumerate(
            self._fetch_histories(stocks, target_trade_date, starts), start=1
        ):
            stock = self._upsert_stock(session, source_stock)
            records = None
            job.retry_count += retries
            job.status = job.stage = "FETCHING"
            if fetched is not None:
                try:
                    if previous_batch and self._has_qfq_revision(
                        session, previous_batch, source_stock, fetched
                    ):
                        # 前复权价格在公司行为后会整体回溯变化；一旦重叠窗口发现修订，
                        # 扩大回拉范围，确保至少覆盖最近 250 个有效交易日。
                        fetched = self.gateway.daily_prices(
                            source_stock,
                            target_trade_date,
                            start_date=None,
                        )
                        # 前复权修订时用完整新历史替换，不拼接旧因子数据。
                        replace_history = True
                    else:
                        replace_history = False
                    records = self._merge_with_previous(
                        session,
                        previous_batch,
                        source_stock,
                        fetched,
                        target_trade_date,
                        replace_history=replace_history,
                    )
                except MarketDataUnavailable:
                    records = None
            if records is None:
                failures.append(source_stock.stock_code)
            else:
                try:
                    self._validate(records)
                except ValueError:
                    failures.append(source_stock.stock_code)
                else:
                    job.status = job.stage = "CALCULATING"
                    previous_records = (
                        self._snapshot_records(session, previous_batch, source_stock)
                        if previous_batch
                        else []
                    )
                    self._persist_prices(
                        session,
                        batch.id,
                        self._price_deltas(previous_records, records),
                    )
                    record_start = min(item.trade_date for item in records)
                    calendar_start = (
                        record_start
                        if calendar_start is None
                        else min(calendar_start, record_start)
                    )
                    self._calculate_stock(session, batch, stock, records, custom_rules)
                    successful_count += 1
            job.completed_count = successful_count
            job.failed_count = len(failures)
            job.failed_items = list(failures)
            job.progress = position / total * 0.95 if total else 0.95
            session.commit()

        batch.completeness_rate = successful_count / total if total else 0.0
        job.completed_count = successful_count
        job.failed_count = len(failures)
        job.failed_items = list(failures)
        job.progress = 1.0
        if batch.completeness_rate < self.minimum_completeness:
            batch.status = "FAILED"
            self._fail_job(job, f"数据完整率 {batch.completeness_rate:.2%} 低于阈值")
            session.commit()
            return SyncResult(job_id=job.id, batch_id=batch.id)

        if calendar_start is None:
            raise MarketDataUnavailable("行情批次缺少可验证的交易日历起点")
        self._persist_trade_calendar(
            session,
            start_date=calendar_start,
            end_date=target_trade_date,
        )

        job.status = job.stage = "VALIDATING"
        session.commit()
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

        self._persist_market_breadth(session, batch)

        job.status = job.stage = "GENERATING_SIGNALS"
        batch.status = "READY"
        SQLAlchemyBatchStore(session).activate_ready_batch(batch.id)
        job.status = job.stage = "READY"
        job.finished_at = datetime.now(UTC)
        session.commit()
        if self.outcome_runner is not None:
            try:
                self.outcome_runner(batch.id)
            except Exception as error:
                logger.error(
                    "候选评价任务执行失败 batch_id=%s error_type=%s",
                    batch.id,
                    type(error).__name__,
                )
        return SyncResult(job_id=job.id, batch_id=batch.id)

    @staticmethod
    def _persist_market_breadth(session: Session, batch: DataBatch) -> None:
        rows = price_rows(
            session, batch.id, trade_date=batch.trade_date, adjustment="raw"
        )
        if not rows:
            rows = price_rows(
                session, batch.id, trade_date=batch.trade_date, adjustment="qfq"
            )
        known_changes = sum(item.pct_change is not None for item in rows)
        is_complete = bool(rows) and known_changes / len(rows) >= 0.99
        existing = session.scalar(
            select(MarketBreadthSnapshot).where(
                MarketBreadthSnapshot.source == batch.source,
                MarketBreadthSnapshot.trade_date == batch.trade_date,
                MarketBreadthSnapshot.scope == "ALL",
            )
        )
        values = {
            "up_count": sum(item.pct_change is not None and item.pct_change > 0 for item in rows),
            "down_count": sum(item.pct_change is not None and item.pct_change < 0 for item in rows),
            "flat_count": sum(item.pct_change == 0 for item in rows),
            "amount": sum(item.amount for item in rows),
            "is_complete": is_complete,
            "fetched_at": datetime.now(UTC),
        }
        if existing is None:
            session.add(
                MarketBreadthSnapshot(
                    source=batch.source,
                    trade_date=batch.trade_date,
                    scope="ALL",
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)

    def _persist_trade_calendar(
        self,
        session: Session,
        *,
        start_date: date,
        end_date: date,
    ) -> None:
        calendar_method = getattr(self.gateway, "trade_calendar", None)
        if callable(calendar_method):
            records = calendar_method(start_date, end_date)
        else:
            # 兼容仅实现旧 is_trade_date 契约的 provider；逐日判断仍由 provider
            # 的权威交易日历完成，不在应用层猜测工作日。
            dates = (
                start_date + timedelta(days=offset)
                for offset in range((end_date - start_date).days + 1)
            )
            records = [
                TradeCalendarRecord(value, self.gateway.is_trade_date(value))
                for value in dates
            ]
        by_date = {item.trade_date: item for item in records}
        expected_dates = {
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
        }
        if set(by_date) != expected_dates:
            raise MarketDataUnavailable("交易日历区间不完整")
        existing = {
            item.trade_date: item
            for item in session.scalars(
                select(TradeCalendar).where(
                    TradeCalendar.market == "CN",
                    TradeCalendar.trade_date.between(start_date, end_date),
                )
            )
        }
        for trade_date in sorted(expected_dates):
            item = existing.get(trade_date)
            if item is None:
                session.add(
                    TradeCalendar(
                        market="CN",
                        trade_date=trade_date,
                        is_open=by_date[trade_date].is_open,
                    )
                )
            else:
                item.is_open = by_date[trade_date].is_open

    def _fetch_histories(self, stocks, target, starts):
        def fetch(stock):
            for attempt in range(3):
                try:
                    rows = self.gateway.daily_prices(
                        stock, target, start_date=starts[(stock.market, stock.stock_code)]
                    )
                    if not rows or max(item.trade_date for item in rows) != target:
                        raise MarketDataUnavailable("来源未返回目标交易日，不能判定为停牌")
                    return stock, rows, attempt
                except MarketDataUnavailable:
                    continue
            return stock, None, 2

        # 只保留一个小窗口，避免全市场历史积压；所有数据库操作仍在主线程。
        with ThreadPoolExecutor(max_workers=self.fetch_workers) as executor:
            for offset in range(0, len(stocks), self.fetch_workers):
                futures = [
                    executor.submit(fetch, stock)
                    for stock in stocks[offset : offset + self.fetch_workers]
                ]
                for future in futures:
                    yield future.result()

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
        stock.stock_name = source.name
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
        *,
        replace_history: bool = False,
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
                for item in price_rows(
                    session,
                    previous_batch.id,
                    market=stock.market,
                    stock_code=stock.stock_code,
                )
            ]
        previous_values = {(item.trade_date, item.adjustment): item for item in previous}
        merged = {} if replace_history else dict(previous_values)
        for item in fetched:
            key = (item.trade_date, item.adjustment)
            old = previous_values.get(key)
            # 已验证的交易所单日涨跌幅不受前复权历史修订影响。
            if item.pct_change is None and old is not None and old.pct_change is not None:
                item = replace(item, pct_change=old.pct_change)
            merged[key] = item
        order = {"raw": 0, "qfq": 1}
        return sorted(merged.values(), key=lambda item: (item.trade_date, order[item.adjustment]))

    @staticmethod
    def _has_qfq_revision(
        session: Session,
        previous_batch: DataBatch,
        stock: StockRecord,
        fetched: list[PriceRecord],
    ) -> bool:
        fetched_qfq = {item.trade_date: item for item in fetched if item.adjustment == "qfq"}
        if not fetched_qfq:
            return False
        previous = {
            item.trade_date: item
            for item in price_rows(
                session,
                previous_batch.id,
                market=stock.market,
                stock_code=stock.stock_code,
                adjustment="qfq",
            )
            if item.trade_date in fetched_qfq
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
        if not records:
            return
        session.execute(
            insert(DailyPrice),
            [
                dict(
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
            ],
        )

    @staticmethod
    def _snapshot_records(
        session: Session, batch: DataBatch, stock: StockRecord
    ) -> list[PriceRecord]:
        return [
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
            for item in price_rows(
                session,
                batch.id,
                market=stock.market,
                stock_code=stock.stock_code,
            )
        ]

    @staticmethod
    def _price_deltas(
        previous: list[PriceRecord], current: list[PriceRecord]
    ) -> list[PriceRecord]:
        prior = {(item.trade_date, item.adjustment): item for item in previous}
        return [
            item
            for item in current
            if prior.get((item.trade_date, item.adjustment)) != item
        ]

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
        indicator_rows = []
        for snapshot in snapshots:
            if snapshot.trade_date != batch.trade_date:
                continue
            values = asdict(snapshot)
            values.pop("trade_date")
            values["unavailable"] = sorted(values["unavailable"])
            indicator_rows.append(
                dict(
                    batch_id=batch.id,
                    market=stock.market,
                    stock_code=stock.stock_code,
                    trade_date=snapshot.trade_date,
                    rule_version=self.rule_version,
                    values=values,
                )
            )
        if indicator_rows:
            session.execute(insert(DailyIndicator), indicator_rows)
        signal_store = SQLAlchemySignalStore(session)
        for evaluation in evaluations:
            if evaluation.trade_date != batch.trade_date:
                continue
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
