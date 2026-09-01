"""区分全市场与自选股实时报价任务，保留既有快照。"""

import sqlalchemy as sa
from alembic import op

revision = "a95e073bf254"
down_revision = "f84d962ae143"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "realtime_refresh",
        sa.Column("scope", sa.String(16), nullable=False, server_default="market"),
    )
    op.add_column(
        "realtime_refresh",
        sa.Column("requested_symbols", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_realtime_refresh_scope", "realtime_refresh", ["scope"])


def downgrade() -> None:
    op.drop_index("ix_realtime_refresh_scope", table_name="realtime_refresh")
    op.drop_column("realtime_refresh", "requested_symbols")
    op.drop_column("realtime_refresh", "scope")
