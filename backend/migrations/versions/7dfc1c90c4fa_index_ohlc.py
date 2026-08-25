"""add index OHLC

Revision ID: 7dfc1c90c4fa
Revises: 4e9a25af5c18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7dfc1c90c4fa"
down_revision: str | None = "4e9a25af5c18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("index_daily") as batch_op:
        batch_op.add_column(sa.Column("open", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("high", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("low", sa.Float(), nullable=False, server_default="0"))
        batch_op.add_column(
            sa.Column(
                "fetched_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            )
        )
    for sort_order, name in enumerate(("短线关注", "中线观察", "风险观察")):
        op.execute(
            sa.text(
                "INSERT INTO watchlist_group (name, sort_order) "
                "SELECT :name, :sort_order WHERE NOT EXISTS "
                "(SELECT 1 FROM watchlist_group WHERE name = :name)"
            ).bindparams(name=name, sort_order=sort_order)
        )


def downgrade() -> None:
    with op.batch_alter_table("index_daily") as batch_op:
        batch_op.drop_column("fetched_at")
        batch_op.drop_column("low")
        batch_op.drop_column("high")
        batch_op.drop_column("open")
