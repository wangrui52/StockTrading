"""add sync and quality metadata

Revision ID: b884b2856227
Revises: 5f02a932346b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b884b2856227"
down_revision: str | None = "5f02a932346b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stock_basic", sa.Column("is_st", sa.Boolean(), nullable=False, server_default="0")
    )
    op.add_column("sync_job", sa.Column("target_trade_date", sa.Date(), nullable=True))
    op.add_column(
        "sync_job", sa.Column("stage", sa.String(32), nullable=False, server_default="PENDING")
    )
    op.add_column("sync_job", sa.Column("progress", sa.Float(), nullable=False, server_default="0"))
    op.add_column(
        "sync_job", sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "sync_job", sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "sync_job", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("sync_job", sa.Column("error_summary", sa.Text(), nullable=True))
    op.add_column("daily_price", sa.Column("turnover_rate", sa.Float(), nullable=True))
    op.add_column(
        "daily_price", sa.Column("is_suspended", sa.Boolean(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("daily_price", "is_suspended")
    op.drop_column("daily_price", "turnover_rate")
    op.drop_column("sync_job", "error_summary")
    op.drop_column("sync_job", "retry_count")
    op.drop_column("sync_job", "failed_count")
    op.drop_column("sync_job", "completed_count")
    op.drop_column("sync_job", "progress")
    op.drop_column("sync_job", "stage")
    op.drop_column("sync_job", "target_trade_date")
    op.drop_column("stock_basic", "is_st")
