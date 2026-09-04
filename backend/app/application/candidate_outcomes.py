import fcntl
import logging
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import Select, and_, case, exists, func, or_, select, tuple_
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.application.batch_snapshot import batch_lineage_ids
from app.domain.outcomes import CompletedOutcome, OutcomeBar, calculate_outcome
from app.infrastructure.models import (
    CandidateOutcome,
    CandidateResult,
    DailyPrice,
    DataBatch,
    OutcomeRun,
    StockBasic,
    TradeCalendar,
)

_HORIZONS = (1, 3, 5)
_TERMINAL_STATUSES = {"COMPLETED", "UNAVAILABLE"}
_OUTCOME_STATUSES = {"PENDING", "COMPLETED", "UNAVAILABLE"}
_ID_CHUNK_SIZE = 800
_PRICE_KEY_CHUNK_SIZE = 250
_CANDIDATE_CHUNK_SIZE = 100
_REVIEW_CHUNK_SIZE = 100
_EVALUATION_LOCK = Lock()
_FAILED_RUN_SUMMARY = "候选评价失败，可重试"
logger = logging.getLogger(__name__)


def validate_calculation_version(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 32:
        raise ValueError("calculation_version must be a nonblank string of at most 32 characters")
    return value


class OutcomeBatchNotReadyError(ValueError):
    """评价批次尚未就绪。"""


class OutcomeBatchNotFoundError(OutcomeBatchNotReadyError, LookupError):
    """评价批次不存在；保留对原批次不可用错误的兼容。"""


class CandidateOutcomeNotFoundError(LookupError):
    """候选记录不存在。"""


class OutcomeRunInProgressError(RuntimeError):
    """同一批次与计算版本已有评价任务执行中。"""


class OutcomeRunStateError(RuntimeError):
    """评价任务处于应用层不支持的持久化状态。"""

    def __init__(self, run_id: int, status: str) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(f"unsupported outcome run state: id={run_id}, status={status}")


class OutcomeRunNotFoundError(LookupError):
    """评价任务不存在。"""


class UnsupportedOutcomeEvaluationBackendError(RuntimeError):
    """数据库后端无法提供评价所需的跨进程串行保证。"""


@dataclass(frozen=True, slots=True)
class OutcomeFilters:
    rule_version: str | None = None
    latest_trading_days: int | None = None
    horizon: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    status: str | None = None
    page: int = 1
    page_size: int = 20

    def __post_init__(self) -> None:
        if self.latest_trading_days is not None and (
            type(self.latest_trading_days) is not int
            or not 1 <= self.latest_trading_days <= 250
        ):
            raise ValueError("latest_trading_days must be between 1 and 250")
        if self.horizon is not None and (
            type(self.horizon) is not int or self.horizon not in _HORIZONS
        ):
            raise ValueError("horizon must be one of 1, 3, 5")
        if self.status is not None and self.status not in _OUTCOME_STATUSES:
            raise ValueError("unsupported outcome status")
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("date_from must not be after date_to")
        if self.page < 1 or self.page_size < 1:
            raise ValueError("page and page_size must be positive")


@dataclass(frozen=True, slots=True)
class OutcomeView:
    id: int
    candidate_result_id: int
    market: str
    stock_code: str
    stock_name: str | None
    source_batch_id: int
    evaluation_batch_id: int | None
    outcome_run_id: int | None
    source_trade_date: date
    rule_version: str
    horizon_trading_days: int
    reference_trade_date: date | None
    evaluation_trade_date: date | None
    expected_evaluation_trade_date: date | None
    reference_price: float | None
    evaluation_price: float | None
    return_rate: float | None
    mfe: float | None
    mae: float | None
    status: str
    unavailable_reason: str | None
    calculation_version: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OutcomePage:
    items: list[OutcomeView]
    total: int
    page: int
    page_size: int
    calculation_version: str
    filters: OutcomeFilters
    data_date: date | None


@dataclass(frozen=True, slots=True)
class OutcomeSummary:
    total: int
    completed: int
    unavailable: int
    pending: int
    sample_size: int
    completion_rate: float
    mean_return_rate: float | None
    median_return_rate: float | None
    positive_return_ratio: float | None
    mean_mfe: float | None
    mean_mae: float | None
    max_drawdown_approx: float | None
    insufficient_sample: bool
    calculation_version: str
    filters: OutcomeFilters
    data_date: date | None


@dataclass(frozen=True, slots=True)
class OutcomeRunView:
    id: int
    evaluation_batch_id: int
    calculation_version: str
    status: str
    expected_count: int
    completed_count: int
    unavailable_count: int
    pending_count: int
    started_at: datetime
    finished_at: datetime | None
    error_summary: str | None


@dataclass(frozen=True, slots=True)
class OutcomePlanView:
    evaluation_batch_id: int
    source: str
    rule_version: str
    expected_count: int
    completed_count: int
    unavailable_count: int
    pending_count: int


class CandidateOutcomeModule:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        calculation_version: str = "outcome-v1",
    ) -> None:
        self._session_factory = session_factory
        self._calculation_version = validate_calculation_version(calculation_version)
        bind = session_factory.kw.get("bind")
        self._database_dialect = bind.dialect.name if bind is not None else None
        database = (
            bind.url.database
            if bind is not None and self._database_dialect == "sqlite"
            else None
        )
        self._evaluation_lock_path = (
            Path(database).expanduser().resolve().with_name(
                f"{Path(database).name}.candidate-outcomes.lock"
            )
            if self._database_dialect == "sqlite"
            and database not in (None, "", ":memory:")
            else None
        )

    @property
    def calculation_version(self) -> str:
        return self._calculation_version

    def evaluate_due_outcomes(self, evaluation_batch_id: int) -> OutcomeRunView:
        with self._evaluation_guard():
            with self._session_factory() as session:
                evaluation_batch = session.get(DataBatch, evaluation_batch_id)
                if evaluation_batch is None:
                    raise OutcomeBatchNotFoundError(evaluation_batch_id)
                if evaluation_batch.status not in {"READY", "READY_WITH_GAPS"}:
                    raise OutcomeBatchNotReadyError(evaluation_batch_id)
                ordered_rules = self._cohort_rules(session, evaluation_batch)
                primary_rule = evaluation_batch.rule_version
            primary_result: OutcomeRunView | None = None
            primary_error: Exception | None = None
            for rule_version in ordered_rules:
                try:
                    result = self._evaluate_rule_outcomes_locked(
                        evaluation_batch_id,
                        rule_version,
                    )
                except Exception as error:
                    if rule_version == primary_rule:
                        primary_error = error
                    else:
                        logger.error(
                            "候选评价规则失败 batch_id=%s rule_version=%s error_type=%s",
                            evaluation_batch_id,
                            rule_version,
                            type(error).__name__,
                        )
                    continue
                if rule_version == primary_rule:
                    primary_result = result
            if primary_error is not None:
                raise primary_error
            if primary_result is None:
                raise RuntimeError("primary outcome cohort did not produce a run")
            return primary_result

    def plan_due_outcomes(
        self,
        evaluation_batch_id: int,
    ) -> tuple[OutcomePlanView, ...]:
        with self._session_factory() as session:
            evaluation_batch = session.get(DataBatch, evaluation_batch_id)
            if evaluation_batch is None:
                raise OutcomeBatchNotFoundError(evaluation_batch_id)
            if evaluation_batch.status not in {"READY", "READY_WITH_GAPS"}:
                raise OutcomeBatchNotReadyError(evaluation_batch_id)
            return tuple(
                self._plan_rule_outcomes(
                    session,
                    evaluation_batch,
                    rule_version,
                )
                for rule_version in self._cohort_rules(session, evaluation_batch)
            )

    @staticmethod
    def _cohort_rules(
        session: Session,
        evaluation_batch: DataBatch,
    ) -> list[str]:
        cohort_rules = set(
            session.scalars(
                select(DataBatch.rule_version)
                .join(
                    CandidateResult,
                    CandidateResult.batch_id == DataBatch.id,
                )
                .where(
                    DataBatch.source == evaluation_batch.source,
                    DataBatch.trade_date <= evaluation_batch.trade_date,
                    DataBatch.status.in_(("READY", "READY_WITH_GAPS")),
                )
                .distinct()
            )
        )
        primary_rule = evaluation_batch.rule_version
        return [primary_rule, *sorted(cohort_rules - {primary_rule})]

    def _plan_rule_outcomes(
        self,
        session: Session,
        evaluation_batch: DataBatch,
        rule_version: str,
    ) -> OutcomePlanView:
        counts = {status: 0 for status in _OUTCOME_STATUSES}
        after_candidate_id = 0
        while True:
            candidates = self._candidate_rows(
                session,
                evaluation_batch,
                rule_version,
                after_candidate_id,
                limit=_CANDIDATE_CHUNK_SIZE,
            )
            if not candidates:
                break
            earliest_source_date = min(
                source_batch.trade_date
                for _candidate, source_batch in candidates
            )
            calendar_dates, open_dates, raw_dates = self._evaluation_date_context(
                session,
                evaluation_batch,
                earliest_source_date,
            )
            due_outcomes: list[tuple[CandidateResult, int, list[date]]] = []
            required_price_keys: set[tuple[str, str, date]] = set()
            for candidate, source_batch in candidates:
                for horizon in _HORIZONS:
                    target_dates = self._authoritative_target_dates(
                        source_date=source_batch.trade_date,
                        horizon=horizon,
                        evaluation_date=evaluation_batch.trade_date,
                        calendar_dates=calendar_dates,
                        open_dates=open_dates,
                        raw_dates=raw_dates,
                    )
                    if target_dates is None:
                        counts["PENDING"] += 1
                        continue
                    due_outcomes.append((candidate, horizon, target_dates))
                    required_price_keys.update(
                        (candidate.market, candidate.stock_code, trade_date)
                        for trade_date in target_dates
                    )
            prices = self._load_price_map(
                session,
                evaluation_batch.id,
                required_price_keys,
            )
            for candidate, horizon, target_dates in due_outcomes:
                calculated = self._calculate_candidate_outcome(
                    [
                        prices.get(
                            (candidate.market, candidate.stock_code, trade_date)
                        )
                        for trade_date in target_dates
                    ],
                    horizon,
                )
                planned_status = (
                    "COMPLETED"
                    if isinstance(calculated, CompletedOutcome)
                    else "UNAVAILABLE"
                )
                counts[planned_status] += 1
            after_candidate_id = candidates[-1][0].id
        expected_count = sum(counts.values())
        return OutcomePlanView(
            evaluation_batch_id=evaluation_batch.id,
            source=evaluation_batch.source,
            rule_version=rule_version,
            expected_count=expected_count,
            completed_count=counts["COMPLETED"],
            unavailable_count=counts["UNAVAILABLE"],
            pending_count=counts["PENDING"],
        )

    def recover_interrupted_runs(self) -> int:
        with self._try_evaluation_guard() as acquired:
            if not acquired:
                return 0
            with self._session_factory.begin() as session:
                runs = session.scalars(
                    select(OutcomeRun).where(OutcomeRun.status == "RUNNING")
                ).all()
                now = datetime.now(UTC)
                for run in runs:
                    run.status = "FAILED"
                    run.finished_at = now
                    run.error_summary = "应用进程中断，可重试"
                return len(runs)

    @contextmanager
    def _evaluation_guard(self) -> Iterator[None]:
        with _EVALUATION_LOCK:
            if self._database_dialect != "sqlite":
                raise UnsupportedOutcomeEvaluationBackendError(
                    "candidate outcome evaluation requires cross-process serialization; "
                    "non-SQLite backends are not supported"
                )
            if self._evaluation_lock_path is None:
                yield
                return
            with self._evaluation_lock_path.open("a+b") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _try_evaluation_guard(self) -> Iterator[bool]:
        acquired_thread_lock = _EVALUATION_LOCK.acquire(blocking=False)
        if not acquired_thread_lock:
            yield False
            return
        try:
            if self._database_dialect != "sqlite":
                raise UnsupportedOutcomeEvaluationBackendError(
                    "candidate outcome recovery requires cross-process serialization; "
                    "non-SQLite backends are not supported"
                )
            if self._evaluation_lock_path is None:
                yield True
                return
            with self._evaluation_lock_path.open("a+b") as lock_file:
                try:
                    fcntl.flock(
                        lock_file.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                except BlockingIOError:
                    yield False
                    return
                try:
                    yield True
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            _EVALUATION_LOCK.release()

    def _evaluate_rule_outcomes_locked(
        self,
        evaluation_batch_id: int,
        rule_version: str,
    ) -> OutcomeRunView:
        run_id, already_completed = self._start_run(
            evaluation_batch_id,
            rule_version,
        )
        if already_completed:
            with self._session_factory() as session:
                run = session.get(OutcomeRun, run_id)
                if run is None:
                    raise RuntimeError("completed outcome run disappeared")
                return self._run_view(run)

        try:
            after_candidate_id = 0
            while True:
                with self._session_factory.begin() as session:
                    next_candidate_id = self._evaluate_candidate_chunk(
                        session,
                        evaluation_batch_id,
                        after_candidate_id,
                        outcome_run_id=run_id,
                        rule_version=rule_version,
                    )
                if next_candidate_id is None:
                    break
                after_candidate_id = next_candidate_id
            with self._session_factory.begin() as session:
                run = session.get(OutcomeRun, run_id)
                if run is None:
                    raise RuntimeError("outcome run disappeared")
                evaluation_batch = session.get(DataBatch, evaluation_batch_id)
                if evaluation_batch is None:
                    raise RuntimeError("evaluation batch disappeared")
                counts = self._source_outcome_counts(
                    session,
                    evaluation_batch,
                    run_id,
                    rule_version,
                )
                run.expected_count = counts[0]
                run.completed_count = counts[1] or 0
                run.unavailable_count = counts[2] or 0
                run.pending_count = counts[3] or 0
                run.status = "COMPLETED"
                run.finished_at = datetime.now(UTC)
                run.error_summary = None
            with self._session_factory() as session:
                completed_run = session.get(OutcomeRun, run_id)
                if completed_run is None:
                    raise RuntimeError("outcome run disappeared after completion")
                return self._run_view(completed_run)
        except Exception as exc:
            with self._session_factory.begin() as session:
                failed_run = session.get(OutcomeRun, run_id)
                if failed_run is not None and failed_run.status == "RUNNING":
                    failed_run.status = "FAILED"
                    failed_run.finished_at = datetime.now(UTC)
                    failed_run.error_summary = _FAILED_RUN_SUMMARY
            logger.error(
                "候选评价失败 batch_id=%s error_type=%s",
                evaluation_batch_id,
                type(exc).__name__,
            )
            raise

    def query_outcomes(self, filters: OutcomeFilters) -> OutcomePage:
        with self._session_factory() as session:
            published_run_ids = self._published_run_ids(session, filters)
            predicates = self._outcome_predicates(filters, published_run_ids)
            query = self._base_query().where(*predicates)
            total, data_date = session.execute(
                select(
                    func.count(CandidateOutcome.id),
                    func.max(CandidateOutcome.source_trade_date),
                ).where(*predicates)
            ).one()
            rows = session.execute(
                query.order_by(
                    CandidateOutcome.source_trade_date.desc(),
                    CandidateOutcome.candidate_result_id,
                    CandidateOutcome.horizon_trading_days,
                )
                .offset((filters.page - 1) * filters.page_size)
                .limit(filters.page_size)
            ).all()
            return OutcomePage(
                items=[self._outcome_view(*row) for row in rows],
                total=total,
                page=filters.page,
                page_size=filters.page_size,
                calculation_version=self._calculation_version,
                filters=filters,
                data_date=data_date,
            )

    def summarize_outcomes(self, filters: OutcomeFilters) -> OutcomeSummary:
        with self._session_factory() as session:
            published_run_ids = self._published_run_ids(session, filters)
            predicates = self._outcome_predicates(filters, published_run_ids)
            completed_condition = CandidateOutcome.status == "COMPLETED"
            completed_return_condition = and_(
                completed_condition, CandidateOutcome.return_rate.is_not(None)
            )
            values = session.execute(
                select(
                    func.count(CandidateOutcome.id),
                    func.sum(case((completed_condition, 1), else_=0)),
                    func.sum(
                        case((CandidateOutcome.status == "UNAVAILABLE", 1), else_=0)
                    ),
                    func.sum(case((CandidateOutcome.status == "PENDING", 1), else_=0)),
                    func.avg(
                        case((completed_condition, CandidateOutcome.return_rate))
                    ),
                    func.sum(
                        case(
                            (
                                and_(
                                    completed_return_condition,
                                    CandidateOutcome.return_rate > 0,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    func.sum(case((completed_return_condition, 1), else_=0)),
                    func.avg(case((completed_condition, CandidateOutcome.mfe))),
                    func.avg(case((completed_condition, CandidateOutcome.mae))),
                    func.min(case((completed_condition, CandidateOutcome.mae))),
                    func.max(CandidateOutcome.source_trade_date),
                ).where(*predicates)
            ).one()
            completed_return_count = values[6] or 0
            median_return_rate = self._median_return_rate(
                session, predicates, completed_return_count
            )
        positive_return_ratio = (
            (values[5] or 0) / completed_return_count
            if completed_return_count
            else None
        )
        total = values[0]
        completed = values[1] or 0
        unavailable = values[2] or 0
        return OutcomeSummary(
            total=total,
            completed=completed,
            unavailable=unavailable,
            pending=values[3] or 0,
            sample_size=completed,
            completion_rate=(completed + unavailable) / total if total else 0,
            mean_return_rate=values[4],
            median_return_rate=median_return_rate,
            positive_return_ratio=positive_return_ratio,
            mean_mfe=values[7],
            mean_mae=values[8],
            max_drawdown_approx=values[9],
            insufficient_sample=completed < 30,
            calculation_version=self._calculation_version,
            filters=filters,
            data_date=values[10],
        )

    def get_candidate_outcomes(self, candidate_result_id: int) -> list[OutcomeView]:
        with self._session_factory() as session:
            if session.get(CandidateResult, candidate_result_id) is None:
                raise CandidateOutcomeNotFoundError(candidate_result_id)
            rows = session.execute(
                self._base_query()
                .where(
                    CandidateOutcome.candidate_result_id == candidate_result_id,
                    CandidateOutcome.calculation_version == self._calculation_version,
                    self._latest_published_snapshot(CandidateOutcome),
                )
                .order_by(CandidateOutcome.horizon_trading_days)
            ).all()
            return [self._outcome_view(*row) for row in rows]

    def get_batch_statuses(self, batch_id: int) -> dict[int, str]:
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    CandidateResult.id,
                    func.count(CandidateOutcome.id),
                    func.sum(
                        case((CandidateOutcome.status == "PENDING", 1), else_=0)
                    ),
                    func.sum(
                        case((CandidateOutcome.status == "COMPLETED", 1), else_=0)
                    ),
                    func.sum(
                        case((CandidateOutcome.status == "UNAVAILABLE", 1), else_=0)
                    ),
                )
                .outerjoin(
                    CandidateOutcome,
                    and_(
                        CandidateOutcome.candidate_result_id == CandidateResult.id,
                        CandidateOutcome.calculation_version
                        == self._calculation_version,
                        self._latest_published_snapshot(CandidateOutcome),
                    ),
                )
                .where(CandidateResult.batch_id == batch_id)
                .group_by(CandidateResult.id)
            ).all()
        statuses = {}
        for candidate_id, total, pending, completed, unavailable in rows:
            if total == 0 or pending == total:
                status = "PENDING"
            elif total == len(_HORIZONS) and completed == total:
                status = "COMPLETED"
            elif total == len(_HORIZONS) and unavailable == total:
                status = "UNAVAILABLE"
            else:
                status = "PARTIAL"
            statuses[candidate_id] = status
        return statuses

    def get_run(self, run_id: int) -> OutcomeRunView:
        with self._session_factory() as session:
            run = session.get(OutcomeRun, run_id)
            if run is None:
                raise OutcomeRunNotFoundError(run_id)
            return self._run_view(run)

    def _start_run(
        self,
        evaluation_batch_id: int,
        rule_version: str,
    ) -> tuple[int, bool]:
        reviewed_run_id, completed_review = self._review_completed_run(
            evaluation_batch_id,
            rule_version,
        )
        with self._session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                session.begin()
            try:
                result = self._start_run_locked(
                    session,
                    evaluation_batch_id,
                    rule_version,
                    reviewed_run_id,
                    completed_review,
                )
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise

    def _start_run_locked(
        self,
        session: Session,
        evaluation_batch_id: int,
        rule_version: str,
        reviewed_run_id: int | None,
        completed_review: bool | None,
    ) -> tuple[int, bool]:
        batch = session.get(DataBatch, evaluation_batch_id)
        if batch is None:
            raise OutcomeBatchNotFoundError(evaluation_batch_id)
        if batch.status not in {"READY", "READY_WITH_GAPS"}:
            raise OutcomeBatchNotReadyError(evaluation_batch_id)
        latest_run = session.scalar(
            select(OutcomeRun)
            .where(
                OutcomeRun.evaluation_batch_id == evaluation_batch_id,
                OutcomeRun.calculation_version == self._calculation_version,
                OutcomeRun.rule_version == rule_version,
            )
            .order_by(OutcomeRun.attempt_no.desc(), OutcomeRun.id.desc())
        )
        if latest_run is not None and latest_run.status == "RUNNING":
            raise OutcomeRunInProgressError(evaluation_batch_id)
        if (
            latest_run is not None
            and latest_run.status == "COMPLETED"
            and reviewed_run_id == latest_run.id
            and completed_review is True
        ):
            return latest_run.id, True
        if latest_run is not None and latest_run.status not in {
            "PENDING",
            "FAILED",
            "COMPLETED",
        }:
            raise OutcomeRunStateError(latest_run.id, latest_run.status)
        run = OutcomeRun(
            evaluation_batch_id=evaluation_batch_id,
            calculation_version=self._calculation_version,
            rule_version=rule_version,
            attempt_no=(latest_run.attempt_no + 1 if latest_run is not None else 1),
            status="RUNNING",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        return run.id, False

    def _review_completed_run(
        self,
        evaluation_batch_id: int,
        rule_version: str,
    ) -> tuple[int | None, bool | None]:
        with self._session_factory() as session:
            batch = session.get(DataBatch, evaluation_batch_id)
            if batch is None:
                return None, None
            run = session.scalar(
                select(OutcomeRun)
                .where(
                    OutcomeRun.evaluation_batch_id == evaluation_batch_id,
                    OutcomeRun.calculation_version == self._calculation_version,
                    OutcomeRun.rule_version == rule_version,
                )
                .order_by(OutcomeRun.attempt_no.desc(), OutcomeRun.id.desc())
            )
            if run is None or run.status != "COMPLETED":
                return (run.id if run is not None else None), None
            counts = self._source_outcome_counts(
                session,
                batch,
                run.id,
                rule_version,
            )
            persisted_counts = (
                run.expected_count,
                run.completed_count,
                run.unavailable_count,
                run.pending_count,
            )
            normalized_counts = (
                counts[0],
                counts[1] or 0,
                counts[2] or 0,
                counts[3] or 0,
            )
            if persisted_counts != normalized_counts:
                return run.id, False
            if self._has_cross_source_outcomes(
                session,
                batch,
                run.id,
                rule_version,
            ):
                return run.id, False
            return run.id, not self._has_invalid_authoritative_outcomes(
                session,
                batch,
                run.id,
                rule_version,
            )

    def _source_outcome_counts(
        self,
        session: Session,
        evaluation_batch: DataBatch,
        outcome_run_id: int,
        rule_version: str,
    ) -> Any:
        return session.execute(
            select(
                func.count(CandidateOutcome.id),
                func.sum(
                    case((CandidateOutcome.status == "COMPLETED", 1), else_=0)
                ),
                func.sum(
                    case((CandidateOutcome.status == "UNAVAILABLE", 1), else_=0)
                ),
                func.sum(
                    case((CandidateOutcome.status == "PENDING", 1), else_=0)
                ),
            )
            .select_from(CandidateOutcome)
            .join(
                CandidateResult,
                CandidateResult.id == CandidateOutcome.candidate_result_id,
            )
            .join(DataBatch, DataBatch.id == CandidateResult.batch_id)
            .where(
                DataBatch.trade_date <= evaluation_batch.trade_date,
                DataBatch.status.in_(("READY", "READY_WITH_GAPS")),
                DataBatch.source == evaluation_batch.source,
                DataBatch.rule_version == rule_version,
                CandidateOutcome.calculation_version == self._calculation_version,
                CandidateOutcome.outcome_run_id == outcome_run_id,
            )
        ).one()

    def _has_cross_source_outcomes(
        self,
        session: Session,
        evaluation_batch: DataBatch,
        outcome_run_id: int,
        rule_version: str,
    ) -> bool:
        source_batch = aliased(DataBatch)
        mismatch_id = session.scalar(
            select(CandidateOutcome.id)
            .join(
                source_batch,
                source_batch.id == CandidateOutcome.source_batch_id,
            )
            .where(
                CandidateOutcome.calculation_version == self._calculation_version,
                CandidateOutcome.outcome_run_id == outcome_run_id,
                or_(
                    source_batch.source != evaluation_batch.source,
                    source_batch.rule_version != rule_version,
                ),
            )
            .limit(1)
        )
        return mismatch_id is not None

    def _has_invalid_authoritative_outcomes(
        self,
        session: Session,
        evaluation_batch: DataBatch,
        outcome_run_id: int,
        rule_version: str,
    ) -> bool:
        source_batch = aliased(DataBatch)
        earliest_source_date = session.scalar(
            select(func.min(source_batch.trade_date))
            .select_from(CandidateOutcome)
            .join(
                source_batch,
                source_batch.id == CandidateOutcome.source_batch_id,
            )
            .where(
                source_batch.source == evaluation_batch.source,
                source_batch.rule_version == rule_version,
                source_batch.trade_date <= evaluation_batch.trade_date,
                source_batch.status.in_(("READY", "READY_WITH_GAPS")),
                CandidateOutcome.calculation_version == self._calculation_version,
                CandidateOutcome.outcome_run_id == outcome_run_id,
            )
        )
        if earliest_source_date is None:
            return False
        calendar_rows = session.execute(
            select(TradeCalendar.trade_date, TradeCalendar.is_open)
            .where(
                TradeCalendar.market == "CN",
                TradeCalendar.trade_date > earliest_source_date,
            )
            .order_by(TradeCalendar.trade_date)
        ).all()
        calendar_dates = [trade_date for trade_date, _is_open in calendar_rows]
        open_dates = [trade_date for trade_date, is_open in calendar_rows if is_open]
        raw_dates = set(
            session.scalars(
                select(DailyPrice.trade_date)
                .where(
                    DailyPrice.batch_id.in_(
                        batch_lineage_ids(session, evaluation_batch.id)
                    ),
                    DailyPrice.adjustment == "raw",
                )
                .distinct()
            )
        )
        after_outcome_id = 0
        while True:
            outcome_rows = session.execute(
                select(
                    CandidateOutcome.id,
                    CandidateOutcome.status,
                    CandidateOutcome.horizon_trading_days,
                    CandidateOutcome.reference_trade_date,
                    CandidateOutcome.evaluation_trade_date,
                    CandidateOutcome.expected_evaluation_trade_date,
                    source_batch.trade_date,
                )
                .join(
                    source_batch,
                    source_batch.id == CandidateOutcome.source_batch_id,
                )
                .where(
                    source_batch.source == evaluation_batch.source,
                    source_batch.rule_version == rule_version,
                    source_batch.trade_date <= evaluation_batch.trade_date,
                    source_batch.status.in_(("READY", "READY_WITH_GAPS")),
                    CandidateOutcome.calculation_version
                    == self._calculation_version,
                    CandidateOutcome.outcome_run_id == outcome_run_id,
                    CandidateOutcome.id > after_outcome_id,
                )
                .order_by(CandidateOutcome.id)
                .limit(_REVIEW_CHUNK_SIZE)
            ).all()
            if not outcome_rows:
                break
            for (
                outcome_id,
                status,
                horizon,
                reference_trade_date,
                evaluation_trade_date,
                expected_evaluation_trade_date,
                source_date,
            ) in outcome_rows:
                expected_target_dates = self._expected_target_dates(
                    source_date=source_date,
                    horizon=horizon,
                    calendar_dates=calendar_dates,
                    open_dates=open_dates,
                )
                expected_date = (
                    expected_target_dates[-1]
                    if expected_target_dates is not None
                    else None
                )
                if expected_evaluation_trade_date != expected_date:
                    return True
                target_dates = self._authoritative_target_dates(
                    source_date=source_date,
                    horizon=horizon,
                    evaluation_date=evaluation_batch.trade_date,
                    calendar_dates=calendar_dates,
                    open_dates=open_dates,
                    raw_dates=raw_dates,
                )
                if status == "PENDING":
                    if target_dates is not None:
                        return True
                elif target_dates is None or (
                    reference_trade_date != target_dates[0]
                    or evaluation_trade_date != target_dates[-1]
                ):
                    return True
                after_outcome_id = outcome_id
        return False

    @staticmethod
    def _candidate_rows(
        session: Session,
        evaluation_batch: DataBatch,
        rule_version: str,
        after_candidate_id: int,
        *,
        limit: int,
    ) -> list[tuple[CandidateResult, DataBatch]]:
        return list(
            session.execute(
                select(CandidateResult, DataBatch)
                .join(DataBatch, DataBatch.id == CandidateResult.batch_id)
                .where(
                    DataBatch.trade_date <= evaluation_batch.trade_date,
                    DataBatch.status.in_(("READY", "READY_WITH_GAPS")),
                    DataBatch.source == evaluation_batch.source,
                    DataBatch.rule_version == rule_version,
                    CandidateResult.id > after_candidate_id,
                )
                .order_by(CandidateResult.id)
                .limit(limit)
            ).all()
        )

    @staticmethod
    def _evaluation_date_context(
        session: Session,
        evaluation_batch: DataBatch,
        earliest_source_date: date,
    ) -> tuple[list[date], list[date], set[date]]:
        calendar_rows = session.execute(
            select(TradeCalendar.trade_date, TradeCalendar.is_open)
            .where(
                TradeCalendar.market == "CN",
                TradeCalendar.trade_date > earliest_source_date,
            )
            .order_by(TradeCalendar.trade_date)
        ).all()
        calendar_dates = [trade_date for trade_date, _is_open in calendar_rows]
        open_dates = [
            trade_date for trade_date, is_open in calendar_rows if is_open
        ]
        raw_dates = set(
            session.scalars(
                select(DailyPrice.trade_date)
                .where(
                    DailyPrice.batch_id.in_(
                        batch_lineage_ids(session, evaluation_batch.id)
                    ),
                    DailyPrice.adjustment == "raw",
                )
                .distinct()
            )
        )
        return calendar_dates, open_dates, raw_dates

    @staticmethod
    def _load_price_map(
        session: Session,
        evaluation_batch_id: int,
        required_price_keys: set[tuple[str, str, date]],
    ) -> dict[tuple[str, str, date], DailyPrice]:
        prices: dict[tuple[str, str, date], DailyPrice] = {}
        lineage = batch_lineage_ids(session, evaluation_batch_id)
        rank = {value: index for index, value in enumerate(lineage)}
        sorted_price_keys = sorted(required_price_keys)
        for price_key_chunk in CandidateOutcomeModule._chunks(
            sorted_price_keys,
            _PRICE_KEY_CHUNK_SIZE,
        ):
            loaded_prices = sorted(
                session.scalars(
                    select(DailyPrice).where(
                        DailyPrice.batch_id.in_(lineage),
                        DailyPrice.adjustment == "raw",
                        tuple_(
                            DailyPrice.market,
                            DailyPrice.stock_code,
                            DailyPrice.trade_date,
                        ).in_(price_key_chunk),
                    )
                ),
                key=lambda item: rank[item.batch_id],
            )
            prices.update(
                {
                    (price.market, price.stock_code, price.trade_date): price
                    for price in loaded_prices
                }
            )
        return prices

    @staticmethod
    def _calculate_candidate_outcome(
        outcome_prices: list[DailyPrice | None],
        horizon: int,
    ) -> Any | None:
        if any(price is None for price in outcome_prices):
            return None
        return calculate_outcome(
            [
                OutcomeBar(
                    trade_date=price.trade_date,
                    open_raw=price.open,
                    high_raw=price.high,
                    low_raw=price.low,
                    close_raw=price.close,
                    is_suspended=price.is_suspended,
                    volume=price.volume,
                )
                for price in outcome_prices
                if price is not None
            ],
            horizon=horizon,  # type: ignore[arg-type]
        )

    def _evaluate_candidate_chunk(
        self,
        session: Session,
        evaluation_batch_id: int,
        after_candidate_id: int,
        *,
        outcome_run_id: int,
        rule_version: str,
    ) -> int | None:
        evaluation_batch = session.get(DataBatch, evaluation_batch_id)
        if evaluation_batch is None:
            raise OutcomeBatchNotReadyError(evaluation_batch_id)
        now = datetime.now(UTC)
        candidate_page = self._candidate_rows(
            session,
            evaluation_batch,
            rule_version,
            after_candidate_id,
            limit=_CANDIDATE_CHUNK_SIZE + 1,
        )
        has_more_candidates = len(candidate_page) > _CANDIDATE_CHUNK_SIZE
        candidates = candidate_page[:_CANDIDATE_CHUNK_SIZE]
        if not candidates:
            return None
        earliest_source_date = min(
            (source_batch.trade_date for _candidate, source_batch in candidates),
            default=evaluation_batch.trade_date,
        )
        calendar_dates, open_dates, raw_dates = self._evaluation_date_context(
            session,
            evaluation_batch,
            earliest_source_date,
        )
        candidate_ids = [candidate.id for candidate, _source_batch in candidates]
        existing_items: list[CandidateOutcome] = []
        for candidate_id_chunk in self._chunks(candidate_ids, _ID_CHUNK_SIZE):
            existing_items.extend(
                session.scalars(
                    select(CandidateOutcome).where(
                        CandidateOutcome.candidate_result_id.in_(candidate_id_chunk),
                        CandidateOutcome.calculation_version
                        == self._calculation_version,
                        CandidateOutcome.outcome_run_id == outcome_run_id,
                    )
                )
            )
        existing = {
            (item.candidate_result_id, item.horizon_trading_days): item
            for item in existing_items
        }
        due_outcomes: list[
            tuple[CandidateOutcome, CandidateResult, int, list[date]]
        ] = []
        required_price_keys: set[tuple[str, str, date]] = set()
        for candidate, source_batch in candidates:
            for horizon in _HORIZONS:
                outcome = existing.get((candidate.id, horizon))
                if outcome is None:
                    outcome = CandidateOutcome(
                        candidate_result_id=candidate.id,
                        source_batch_id=source_batch.id,
                        evaluation_batch_id=evaluation_batch_id,
                        outcome_run_id=outcome_run_id,
                        source_trade_date=source_batch.trade_date,
                        rule_version=source_batch.rule_version,
                        horizon_trading_days=horizon,
                        calculation_version=self._calculation_version,
                        status="PENDING",
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(outcome)
                expected_target_dates = self._expected_target_dates(
                    source_date=source_batch.trade_date,
                    horizon=horizon,
                    calendar_dates=calendar_dates,
                    open_dates=open_dates,
                )
                expected_evaluation_trade_date = (
                    expected_target_dates[-1]
                    if expected_target_dates is not None
                    else None
                )
                target_dates = self._authoritative_target_dates(
                    source_date=source_batch.trade_date,
                    horizon=horizon,
                    evaluation_date=evaluation_batch.trade_date,
                    calendar_dates=calendar_dates,
                    open_dates=open_dates,
                    raw_dates=raw_dates,
                )
                if target_dates is None:
                    self._reset_pending(
                        outcome,
                        now,
                        evaluation_batch_id=evaluation_batch_id,
                        expected_evaluation_trade_date=(
                            expected_evaluation_trade_date
                        ),
                    )
                    continue
                outcome.expected_evaluation_trade_date = (
                    expected_evaluation_trade_date
                )
                if outcome.status in _TERMINAL_STATUSES:
                    if (
                        outcome.reference_trade_date == target_dates[0]
                        and outcome.evaluation_trade_date == target_dates[-1]
                    ):
                        continue
                    self._reset_pending(
                        outcome,
                        now,
                        evaluation_batch_id=evaluation_batch_id,
                        expected_evaluation_trade_date=(
                            expected_evaluation_trade_date
                        ),
                    )
                outcome.reference_trade_date = target_dates[0]
                outcome.evaluation_trade_date = target_dates[-1]
                due_outcomes.append((outcome, candidate, horizon, target_dates))
                required_price_keys.update(
                    (candidate.market, candidate.stock_code, trade_date)
                    for trade_date in target_dates
                )

        prices = self._load_price_map(
            session,
            evaluation_batch_id,
            required_price_keys,
        )

        for outcome, candidate, horizon, target_dates in due_outcomes:
            outcome_prices = [
                prices.get((candidate.market, candidate.stock_code, trade_date))
                for trade_date in target_dates
            ]
            calculated = self._calculate_candidate_outcome(outcome_prices, horizon)
            if calculated is None:
                self._mark_unavailable(
                    outcome, evaluation_batch_id, "PRICE_DATA_MISSING", now
                )
                continue
            if isinstance(calculated, CompletedOutcome):
                outcome.status = "COMPLETED"
                outcome.evaluation_batch_id = evaluation_batch_id
                outcome.reference_trade_date = calculated.reference_date
                outcome.evaluation_trade_date = calculated.evaluation_date
                outcome.reference_price = calculated.reference_price
                outcome.evaluation_price = calculated.evaluation_price
                outcome.return_rate = calculated.return_rate
                outcome.mfe = calculated.mfe
                outcome.mae = calculated.mae
                outcome.unavailable_reason = None
                outcome.updated_at = now
            else:
                self._mark_unavailable(
                    outcome, evaluation_batch_id, calculated.reason_code, now
                )
        return candidates[-1][0].id if has_more_candidates else None

    @staticmethod
    def _authoritative_target_dates(
        *,
        source_date: date,
        horizon: int,
        evaluation_date: date,
        calendar_dates: list[date],
        open_dates: list[date],
        raw_dates: set[date],
    ) -> list[date] | None:
        target_dates = CandidateOutcomeModule._expected_target_dates(
            source_date=source_date,
            horizon=horizon,
            calendar_dates=calendar_dates,
            open_dates=open_dates,
        )
        if target_dates is None or target_dates[-1] > evaluation_date:
            return None
        if any(trade_date not in raw_dates for trade_date in target_dates):
            return None
        return target_dates

    @staticmethod
    def _expected_target_dates(
        *,
        source_date: date,
        horizon: int,
        calendar_dates: list[date],
        open_dates: list[date],
    ) -> list[date] | None:
        first_open_index = bisect_right(open_dates, source_date)
        target_dates = open_dates[first_open_index : first_open_index + horizon]
        if len(target_dates) < horizon:
            return None
        first_calendar_date = source_date + timedelta(days=1)
        first_calendar_index = bisect_left(calendar_dates, first_calendar_date)
        target_calendar_end = bisect_right(calendar_dates, target_dates[-1])
        expected_calendar_days = (target_dates[-1] - first_calendar_date).days + 1
        if target_calendar_end - first_calendar_index != expected_calendar_days:
            return None
        return target_dates

    @staticmethod
    def _reset_pending(
        outcome: CandidateOutcome,
        now: datetime,
        *,
        evaluation_batch_id: int | None = None,
        expected_evaluation_trade_date: date | None = None,
    ) -> None:
        outcome.evaluation_batch_id = evaluation_batch_id
        outcome.reference_trade_date = None
        outcome.evaluation_trade_date = None
        outcome.expected_evaluation_trade_date = expected_evaluation_trade_date
        outcome.reference_price = None
        outcome.evaluation_price = None
        outcome.return_rate = None
        outcome.mfe = None
        outcome.mae = None
        outcome.status = "PENDING"
        outcome.unavailable_reason = None
        outcome.updated_at = now

    @staticmethod
    def _chunks(values: list[Any], size: int) -> list[list[Any]]:
        return [
            values[index : index + size]
            for index in range(0, len(values), size)
        ]

    @staticmethod
    def _mark_unavailable(
        outcome: CandidateOutcome,
        evaluation_batch_id: int,
        reason: str,
        now: datetime,
    ) -> None:
        outcome.status = "UNAVAILABLE"
        outcome.evaluation_batch_id = evaluation_batch_id
        outcome.reference_price = None
        outcome.evaluation_price = None
        outcome.return_rate = None
        outcome.mfe = None
        outcome.mae = None
        outcome.unavailable_reason = reason
        outcome.updated_at = now

    def _outcome_predicates(
        self,
        filters: OutcomeFilters,
        published_run_ids: tuple[int, ...],
    ) -> list[Any]:
        predicates = self._window_scope_predicates(CandidateOutcome, filters)
        predicates.append(CandidateOutcome.outcome_run_id.in_(published_run_ids))
        predicates.append(
            self._published_snapshot_consistency(CandidateOutcome)
        )
        if filters.latest_trading_days is not None:
            active_batch_date = (
                select(DataBatch.trade_date)
                .where(DataBatch.is_active.is_(True))
                .order_by(DataBatch.trade_date.desc(), DataBatch.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            latest_ready_batch_date = (
                select(DataBatch.trade_date)
                .where(DataBatch.status.in_(("READY", "READY_WITH_GAPS")))
                .order_by(DataBatch.trade_date.desc(), DataBatch.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            effective_batch_date = func.coalesce(
                active_batch_date, latest_ready_batch_date
            )
            date_predicates = [
                TradeCalendar.market == "CN",
                TradeCalendar.is_open.is_(True),
                TradeCalendar.trade_date <= effective_batch_date,
            ]
            if filters.date_from is not None:
                date_predicates.append(
                    TradeCalendar.trade_date >= filters.date_from
                )
            if filters.date_to is not None:
                date_predicates.append(
                    TradeCalendar.trade_date <= filters.date_to
                )
            latest_dates = (
                select(TradeCalendar.trade_date)
                .where(*date_predicates)
                .order_by(TradeCalendar.trade_date.desc())
                .limit(filters.latest_trading_days)
            )
            predicates.append(CandidateOutcome.source_trade_date.in_(latest_dates))
        if filters.horizon is not None:
            predicates.append(
                CandidateOutcome.horizon_trading_days == filters.horizon
            )
        if filters.status is not None:
            predicates.append(CandidateOutcome.status == filters.status)
        return predicates

    def _published_run_ids(
        self,
        session: Session,
        filters: OutcomeFilters,
    ) -> tuple[int, ...]:
        active_source = (
            select(DataBatch.source)
            .where(DataBatch.is_active.is_(True))
            .order_by(DataBatch.trade_date.desc(), DataBatch.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        latest_ready_source = (
            select(DataBatch.source)
            .where(DataBatch.status.in_(("READY", "READY_WITH_GAPS")))
            .order_by(DataBatch.trade_date.desc(), DataBatch.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        effective_source = func.coalesce(active_source, latest_ready_source)
        ranked_runs = (
            select(
                OutcomeRun.id.label("run_id"),
                func.row_number()
                .over(
                    partition_by=(DataBatch.source, OutcomeRun.rule_version),
                    order_by=(
                        DataBatch.trade_date.desc(),
                        DataBatch.id.desc(),
                        OutcomeRun.attempt_no.desc(),
                        OutcomeRun.id.desc(),
                    ),
                )
                .label("published_rank"),
            )
            .join(DataBatch, DataBatch.id == OutcomeRun.evaluation_batch_id)
            .where(
                OutcomeRun.calculation_version == self._calculation_version,
                OutcomeRun.status == "COMPLETED",
                DataBatch.source == effective_source,
            )
        )
        if filters.rule_version is not None:
            ranked_runs = ranked_runs.where(
                OutcomeRun.rule_version == filters.rule_version
            )
        ranked = ranked_runs.subquery()
        return tuple(
            session.scalars(
                select(ranked.c.run_id).where(ranked.c.published_rank == 1)
            ).all()
        )

    @staticmethod
    def _latest_published_snapshot(outcome: Any) -> Any:
        published_run = aliased(OutcomeRun)
        published_batch = aliased(DataBatch)
        source_batch = aliased(DataBatch)
        source = (
            select(source_batch.source)
            .where(source_batch.id == outcome.source_batch_id)
            .correlate(outcome)
            .scalar_subquery()
        )
        latest_outcome_run_id = (
            select(published_run.id)
            .join(
                published_batch,
                published_batch.id == published_run.evaluation_batch_id,
            )
            .where(
                published_run.calculation_version == outcome.calculation_version,
                published_run.status == "COMPLETED",
                published_run.rule_version == outcome.rule_version,
                published_batch.source == source,
            )
            .order_by(
                published_batch.trade_date.desc(),
                published_batch.id.desc(),
                published_run.attempt_no.desc(),
                published_run.id.desc(),
            )
            .limit(1)
            .scalar_subquery()
        )
        return and_(
            outcome.outcome_run_id == latest_outcome_run_id,
            CandidateOutcomeModule._published_snapshot_consistency(outcome),
        )

    @staticmethod
    def _published_snapshot_consistency(outcome: Any) -> Any:
        candidate = aliased(CandidateResult)
        source_batch = aliased(DataBatch)
        published_run = aliased(OutcomeRun)
        evaluation_batch = aliased(DataBatch)
        return exists(
            select(1)
            .select_from(candidate)
            .join(source_batch, source_batch.id == candidate.batch_id)
            .join(
                published_run,
                published_run.id == outcome.outcome_run_id,
            )
            .join(
                evaluation_batch,
                evaluation_batch.id == published_run.evaluation_batch_id,
            )
            .where(
                candidate.id == outcome.candidate_result_id,
                source_batch.id == outcome.source_batch_id,
                source_batch.source == evaluation_batch.source,
                source_batch.rule_version == published_run.rule_version,
                outcome.evaluation_batch_id == published_run.evaluation_batch_id,
                outcome.rule_version == published_run.rule_version,
                outcome.calculation_version
                == published_run.calculation_version,
            )
            .correlate(outcome)
        )

    def _window_scope_predicates(
        self,
        outcome: Any,
        filters: OutcomeFilters,
    ) -> list[Any]:
        predicates = [outcome.calculation_version == self._calculation_version]
        if filters.rule_version is not None:
            predicates.append(outcome.rule_version == filters.rule_version)
        if filters.date_from is not None:
            predicates.append(outcome.source_trade_date >= filters.date_from)
        if filters.date_to is not None:
            predicates.append(outcome.source_trade_date <= filters.date_to)
        return predicates

    @staticmethod
    def _median_return_rate(
        session: Session,
        predicates: list[Any],
        completed_return_count: int,
    ) -> float | None:
        if completed_return_count == 0:
            return None
        middle_offset = (completed_return_count - 1) // 2
        middle_count = 2 if completed_return_count % 2 == 0 else 1
        middle_values = session.scalars(
            select(CandidateOutcome.return_rate)
            .where(
                *predicates,
                CandidateOutcome.status == "COMPLETED",
                CandidateOutcome.return_rate.is_not(None),
            )
            .order_by(CandidateOutcome.return_rate)
            .offset(middle_offset)
            .limit(middle_count)
        ).all()
        if not middle_values:
            return None
        return sum(middle_values) / len(middle_values)

    @staticmethod
    def _base_query() -> Select:
        return (
            select(CandidateOutcome, CandidateResult, StockBasic.stock_name)
            .join(
                CandidateResult,
                CandidateResult.id == CandidateOutcome.candidate_result_id,
            )
            .outerjoin(
                StockBasic,
                and_(
                    StockBasic.market == CandidateResult.market,
                    StockBasic.stock_code == CandidateResult.stock_code,
                ),
            )
        )

    @staticmethod
    def _outcome_view(
        outcome: CandidateOutcome,
        candidate: CandidateResult,
        stock_name: str | None,
    ) -> OutcomeView:
        return OutcomeView(
            id=outcome.id,
            candidate_result_id=outcome.candidate_result_id,
            market=candidate.market,
            stock_code=candidate.stock_code,
            stock_name=stock_name,
            source_batch_id=outcome.source_batch_id,
            evaluation_batch_id=outcome.evaluation_batch_id,
            outcome_run_id=outcome.outcome_run_id,
            source_trade_date=outcome.source_trade_date,
            rule_version=outcome.rule_version,
            horizon_trading_days=outcome.horizon_trading_days,
            reference_trade_date=outcome.reference_trade_date,
            evaluation_trade_date=outcome.evaluation_trade_date,
            expected_evaluation_trade_date=(
                outcome.expected_evaluation_trade_date
            ),
            reference_price=outcome.reference_price,
            evaluation_price=outcome.evaluation_price,
            return_rate=outcome.return_rate,
            mfe=outcome.mfe,
            mae=outcome.mae,
            status=outcome.status,
            unavailable_reason=outcome.unavailable_reason,
            calculation_version=outcome.calculation_version,
            updated_at=outcome.updated_at,
        )

    @staticmethod
    def _run_view(run: OutcomeRun) -> OutcomeRunView:
        return OutcomeRunView(
            id=run.id,
            evaluation_batch_id=run.evaluation_batch_id,
            calculation_version=run.calculation_version,
            status=run.status,
            expected_count=run.expected_count,
            completed_count=run.completed_count,
            unavailable_count=run.unavailable_count,
            pending_count=run.pending_count,
            started_at=run.started_at,
            finished_at=run.finished_at,
            error_summary=run.error_summary,
        )
