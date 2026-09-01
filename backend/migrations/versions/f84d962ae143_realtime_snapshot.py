"""独立全市场实时报价任务与快照，不修改日线数据。"""

import sqlalchemy as sa
from alembic import op

revision = "f84d962ae143"
down_revision = "e73c851fd032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "realtime_refresh",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "realtime_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("quotes", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("realtime_snapshot")
    op.drop_table("realtime_refresh")
