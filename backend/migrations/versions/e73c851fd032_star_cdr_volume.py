"""修正早期腾讯批次中科创板 CDR 689 前缀的成交量单位。"""

from alembic import op

revision = "e73c851fd032"
down_revision = "d62b740ec921"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 腾讯 688 与 689 均以股返回；仅早期本适配器的 689 数据被误乘 100。
    # 全历史按同一比例修正，不改变成交额、复权价格或成交量比率指标。
    op.execute("""
        UPDATE daily_price SET volume = CAST(volume / 100 AS INTEGER)
        WHERE market = 'SH' AND stock_code LIKE '689%'
        AND batch_id IN (SELECT id FROM data_batch WHERE source = 'tencent-sina-v1')
    """)


def downgrade() -> None:
    raise RuntimeError("单位修正不可逆回错误值；如需整体回滚请使用升级前备份")
