"""Distinguish candidate and watchlist AI analysis runs.

Revision ID: 31d4e8a7c2b9
Revises: 2f7c9d41a6e3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "31d4e8a7c2b9"
down_revision: str | None = "2f7c9d41a6e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_recommendation_run") as batch_op:
        batch_op.add_column(
            sa.Column(
                "scope",
                sa.String(length=16),
                nullable=False,
                server_default="candidate",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_recommendation_run") as batch_op:
        batch_op.drop_column("scope")
