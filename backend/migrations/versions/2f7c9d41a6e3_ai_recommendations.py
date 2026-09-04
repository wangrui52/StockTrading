"""Store versioned Codex CLI candidate recommendations.

Revision ID: 2f7c9d41a6e3
Revises: 8d3a7c91e4f2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2f7c9d41a6e3"
down_revision: str | None = "8d3a7c91e4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_recommendation_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["data_batch.id"]),
        sa.UniqueConstraint("batch_id", "version"),
    )
    op.create_index(
        "ix_ai_recommendation_run_batch_id", "ai_recommendation_run", ["batch_id"]
    )
    op.create_table(
        "ai_recommendation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("stock_code", sa.String(length=16), nullable=False),
        sa.Column("recommendation", sa.String(length=16), nullable=False),
        sa.Column("ai_score", sa.Integer(), nullable=False),
        sa.Column("horizon_trading_days", sa.Integer(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("invalidation", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["ai_recommendation_run.id"]),
        sa.UniqueConstraint("run_id", "market", "stock_code"),
    )
    op.create_index("ix_ai_recommendation_run_id", "ai_recommendation", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_recommendation_run_id", table_name="ai_recommendation")
    op.drop_table("ai_recommendation")
    op.drop_index("ix_ai_recommendation_run_batch_id", table_name="ai_recommendation_run")
    op.drop_table("ai_recommendation_run")
