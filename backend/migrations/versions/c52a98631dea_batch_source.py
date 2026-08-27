"""记录批次来源，识别已有演示批次而不删除数据。"""

import sqlalchemy as sa
from alembic import op

revision = "c52a98631dea"
down_revision = "a9f3c8d2e710"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "data_batch", sa.Column("source", sa.String(64), nullable=False, server_default="unknown")
    )
    op.execute("""
        UPDATE data_batch SET source = 'demo-v1'
        WHERE id IN (
            SELECT DISTINCT p.batch_id FROM daily_price p JOIN stock_basic s
            ON p.market = s.market AND p.stock_code = s.stock_code
            WHERE s.industry = '固定样本' AND s.stock_name LIKE '示例股份%'
        )
    """)


def downgrade() -> None:
    op.drop_column("data_batch", "source")
