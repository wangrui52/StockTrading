"""add structured operation log

Revision ID: a9f3c8d2e710
Revises: 7dfc1c90c4fa
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9f3c8d2e710"
down_revision: str | None = "7dfc1c90c4fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_name", sa.String(64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("page", sa.String(64), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=True),
        sa.Column("market", sa.String(8), nullable=True),
        sa.Column("stock_code", sa.String(16), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["batch_id"], ["data_batch.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_operation_log_event_name", "operation_log", ["event_name"])
    op.create_index("ix_operation_log_occurred_at", "operation_log", ["occurred_at"])
    op.create_index("ix_operation_log_batch_id", "operation_log", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_operation_log_batch_id", table_name="operation_log")
    op.drop_index("ix_operation_log_occurred_at", table_name="operation_log")
    op.drop_index("ix_operation_log_event_name", table_name="operation_log")
    op.drop_table("operation_log")
