import argparse
import json
import sys
from collections.abc import Sequence
from typing import TextIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.application.candidate_outcomes import (
    CandidateOutcomeModule,
    OutcomePlanView,
    validate_calculation_version,
)
from app.infrastructure.database import create_sqlite_session_factory
from app.infrastructure.models import (
    CandidateOutcome,
    CandidateResult,
    DataBatch,
    OutcomeRun,
)

_VALID_BATCH_STATUSES = ("READY", "READY_WITH_GAPS")
_OUTCOME_STATUSES = ("PENDING", "COMPLETED", "UNAVAILABLE")


def _plan_payload(plan: OutcomePlanView) -> dict[str, int | str]:
    return {
        "evaluation_batch_id": plan.evaluation_batch_id,
        "source": plan.source,
        "rule_version": plan.rule_version,
        "expected_count": plan.expected_count,
        "completed_count": plan.completed_count,
        "unavailable_count": plan.unavailable_count,
        "pending_count": plan.pending_count,
    }


def _write_fatal_error(error_output: TextIO, *, phase: str, error: Exception) -> None:
    message = "回填初始化失败" if phase == "initialization" else "回填计划读取失败"
    print(
        json.dumps(
            {
                "event": "candidate_outcome_backfill_failed",
                "phase": phase,
                "error_type": type(error).__name__,
                "message": message,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=error_output,
    )


def run_backfill(
    session_factory: sessionmaker[Session],
    *,
    dry_run: bool,
    calculation_version: str,
    output: TextIO,
    error_output: TextIO,
) -> int:
    calculation_version = validate_calculation_version(calculation_version)
    try:
        with session_factory() as session:
            batches = session.scalars(
                select(DataBatch)
                .where(DataBatch.status.in_(_VALID_BATCH_STATUSES))
                .order_by(DataBatch.trade_date, DataBatch.id)
            ).all()
            candidate_count = session.scalar(
                select(func.count(CandidateResult.id))
                .join(DataBatch, DataBatch.id == CandidateResult.batch_id)
                .where(DataBatch.status.in_(_VALID_BATCH_STATUSES))
            )
            completed_runs = session.execute(
                select(OutcomeRun, DataBatch)
                .join(DataBatch, DataBatch.id == OutcomeRun.evaluation_batch_id)
                .where(
                    OutcomeRun.calculation_version == calculation_version,
                    OutcomeRun.status == "COMPLETED",
                )
                .order_by(
                    DataBatch.trade_date,
                    DataBatch.id,
                    OutcomeRun.attempt_no,
                    OutcomeRun.id,
                )
            ).all()
            published_runs = {
                (batch.source, run.rule_version): run
                for run, batch in completed_runs
            }
            published_run_ids = [run.id for run in published_runs.values()]
            source_batch = aliased(DataBatch)
            evaluation_batch = aliased(DataBatch)
            status_rows = (
                session.execute(
                    select(CandidateOutcome.status, func.count(CandidateOutcome.id))
                    .join(
                        OutcomeRun,
                        OutcomeRun.id == CandidateOutcome.outcome_run_id,
                    )
                    .join(
                        source_batch,
                        source_batch.id == CandidateOutcome.source_batch_id,
                    )
                    .join(
                        evaluation_batch,
                        evaluation_batch.id == OutcomeRun.evaluation_batch_id,
                    )
                    .where(
                        CandidateOutcome.outcome_run_id.in_(published_run_ids),
                        CandidateOutcome.calculation_version
                        == calculation_version,
                        CandidateOutcome.evaluation_batch_id
                        == OutcomeRun.evaluation_batch_id,
                        CandidateOutcome.rule_version == OutcomeRun.rule_version,
                        source_batch.source == evaluation_batch.source,
                        source_batch.rule_version == OutcomeRun.rule_version,
                    )
                    .group_by(CandidateOutcome.status)
                ).all()
                if published_run_ids
                else []
            )
        module = CandidateOutcomeModule(
            session_factory,
            calculation_version=calculation_version,
        )
        projected_plans = (
            [
                plan
                for batch in batches
                for plan in module.plan_due_outcomes(batch.id)
            ]
            if dry_run
            else []
        )
    except Exception as error:
        _write_fatal_error(error_output, phase="planning", error=error)
        return 2

    counts = {status: 0 for status in _OUTCOME_STATUSES}
    counts.update(dict(status_rows))
    if dry_run:
        projected_totals = {
            "expected_count": sum(
                plan.expected_count for plan in projected_plans
            ),
            "completed_count": sum(
                plan.completed_count for plan in projected_plans
            ),
            "unavailable_count": sum(
                plan.unavailable_count for plan in projected_plans
            ),
            "pending_count": sum(
                plan.pending_count for plan in projected_plans
            ),
        }
        summary = {
            "mode": "dry-run",
            "valid_batch_count": len(batches),
            "candidate_count": candidate_count or 0,
            "final_logical_outcome_rows": (candidate_count or 0) * 3,
            "current_published_status_counts": counts,
            "current_published_snapshot_count": len(published_run_ids),
            "current_published_outcome_rows": sum(counts.values()),
            "projected_cohorts": [
                _plan_payload(plan) for plan in projected_plans
            ],
            "projected_totals": projected_totals,
            "evaluation_batch_ids": [batch.id for batch in batches],
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), file=output)
        return 0

    failed = False
    for batch in batches:
        try:
            run = module.evaluate_due_outcomes(batch.id)
        except Exception as error:
            failed = True
            print(
                json.dumps(
                    {
                        "evaluation_batch_id": batch.id,
                        "error_type": type(error).__name__,
                        "message": "batch evaluation failed",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=error_output,
            )
            continue
        print(
            json.dumps(
                {
                    "evaluation_batch_id": batch.id,
                    "run_id": run.id,
                    "status": run.status,
                    "expected_count": run.expected_count,
                    "completed_count": run.completed_count,
                    "unavailable_count": run.unavailable_count,
                    "pending_count": run.pending_count,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=output,
        )
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回填历史候选的后续表现")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入数据库")
    parser.add_argument("--database-url", help="覆盖 DATABASE_URL")
    parser.add_argument(
        "--calculation-version",
        default="outcome-v1",
        type=_calculation_version_argument,
        help="评价计算版本（默认 outcome-v1）",
    )
    return parser


def _calculation_version_argument(value: str) -> str:
    try:
        return validate_calculation_version(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("计算版本必须为1到32个非空字符") from error


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        factory = create_sqlite_session_factory(args.database_url)
    except Exception as error:
        _write_fatal_error(sys.stderr, phase="initialization", error=error)
        return 2
    return run_backfill(
        factory,
        dry_run=args.dry_run,
        calculation_version=args.calculation_version,
        output=sys.stdout,
        error_output=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
