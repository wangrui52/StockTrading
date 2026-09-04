"""Add parent lineage for incremental data batches.

Revision ID: 6b8f4df0a2c1
Revises: f3c12a7e9b40
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b8f4df0a2c1"
down_revision: str | None = "f3c12a7e9b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("data_batch") as batch_op:
        batch_op.add_column(sa.Column("parent_batch_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_data_batch_parent_batch_id", "data_batch", ["parent_batch_id"], ["id"]
        )
        batch_op.create_index("ix_data_batch_parent_batch_id", ["parent_batch_id"])
    op.create_table(
        "market_breadth_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="ALL"),
        sa.Column("up_count", sa.Integer(), nullable=False),
        sa.Column("down_count", sa.Integer(), nullable=False),
        sa.Column("flat_count", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint("source", "trade_date", "scope"),
    )
    op.create_index(
        "ix_market_breadth_snapshot_trade_date",
        "market_breadth_snapshot",
        ["trade_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_market_breadth_snapshot_trade_date", table_name="market_breadth_snapshot"
    )
    op.drop_table("market_breadth_snapshot")
    with op.batch_alter_table("data_batch") as batch_op:
        batch_op.drop_index("ix_data_batch_parent_batch_id")
        batch_op.drop_constraint("fk_data_batch_parent_batch_id", type_="foreignkey")
        batch_op.drop_column("parent_batch_id")
