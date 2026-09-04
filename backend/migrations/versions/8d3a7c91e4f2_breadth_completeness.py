"""Record unavailable market breadth without rescanning prices.

Revision ID: 8d3a7c91e4f2
Revises: 6b8f4df0a2c1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d3a7c91e4f2"
down_revision: str | None = "6b8f4df0a2c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("market_breadth_snapshot") as batch_op:
        batch_op.add_column(
            sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("market_breadth_snapshot") as batch_op:
        batch_op.drop_column("is_complete")
