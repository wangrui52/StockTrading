"""Persist candidate outcomes and idempotent evaluation runs.

Revision ID: f3c12a7e9b40
Revises: a95e073bf254
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3c12a7e9b40"
down_revision: str | None = "a95e073bf254"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outcome_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evaluation_batch_id", sa.Integer(), nullable=False),
        sa.Column(
            "calculation_version",
            sa.String(32),
            nullable=False,
            server_default="outcome-v1",
        ),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("expected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unavailable_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["evaluation_batch_id"], ["data_batch.id"]),
        sa.UniqueConstraint(
            "evaluation_batch_id",
            "calculation_version",
            "rule_version",
            "attempt_no",
            name="uq_outcome_run_batch_version",
        ),
    )

    op.create_table(
        "candidate_outcome",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("candidate_result_id", sa.Integer(), nullable=False),
        sa.Column("source_batch_id", sa.Integer(), nullable=False),
        sa.Column("evaluation_batch_id", sa.Integer(), nullable=True),
        sa.Column("outcome_run_id", sa.Integer(), nullable=True),
        sa.Column("source_trade_date", sa.Date(), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("horizon_trading_days", sa.Integer(), nullable=False),
        sa.Column("reference_trade_date", sa.Date(), nullable=True),
        sa.Column("evaluation_trade_date", sa.Date(), nullable=True),
        sa.Column("expected_evaluation_trade_date", sa.Date(), nullable=True),
        sa.Column("reference_price", sa.Float(), nullable=True),
        sa.Column("evaluation_price", sa.Float(), nullable=True),
        sa.Column("return_rate", sa.Float(), nullable=True),
        sa.Column("mfe", sa.Float(), nullable=True),
        sa.Column("mae", sa.Float(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("unavailable_reason", sa.String(64), nullable=True),
        sa.Column(
            "calculation_version",
            sa.String(32),
            nullable=False,
            server_default="outcome-v1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(["candidate_result_id"], ["candidate_result.id"]),
        sa.ForeignKeyConstraint(["source_batch_id"], ["data_batch.id"]),
        sa.ForeignKeyConstraint(["evaluation_batch_id"], ["data_batch.id"]),
        sa.ForeignKeyConstraint(["outcome_run_id"], ["outcome_run.id"]),
        sa.UniqueConstraint(
            "candidate_result_id",
            "horizon_trading_days",
            "calculation_version",
            "outcome_run_id",
            name="uq_candidate_outcome_snapshot",
        ),
    )
    op.create_index(
        "ix_candidate_outcome_rule_horizon_date_status",
        "candidate_outcome",
        ["rule_version", "horizon_trading_days", "source_trade_date", "status"],
    )
    op.create_index(
        "ix_candidate_outcome_window",
        "candidate_outcome",
        ["calculation_version", "rule_version", "source_trade_date"],
    )
    op.create_index(
        "ix_candidate_outcome_snapshot",
        "candidate_outcome",
        ["outcome_run_id", "calculation_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_outcome_snapshot",
        table_name="candidate_outcome",
    )
    op.drop_index(
        "ix_candidate_outcome_window",
        table_name="candidate_outcome",
    )
    op.drop_index(
        "ix_candidate_outcome_rule_horizon_date_status",
        table_name="candidate_outcome",
    )
    op.drop_table("candidate_outcome")
    op.drop_table("outcome_run")
