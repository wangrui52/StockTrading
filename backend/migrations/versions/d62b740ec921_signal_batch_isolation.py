"""Keep signal payloads isolated by batch without losing alert confirmations."""

from alembic import op

revision = "d62b740ec921"
down_revision = "c52a98631dea"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table(
        "signal_event", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}
    ) as batch:
        batch.drop_constraint("uq_signal_event_market", type_="unique")
        batch.create_unique_constraint(
            "uq_signal_batch_event",
            ["batch_id", "market", "stock_code", "trade_date", "rule_code", "rule_version"],
        )


def downgrade() -> None:
    # 多个批次可能已包含相同事件；不能通过删除历史来满足旧唯一键。
    raise RuntimeError("请从升级前备份恢复，不能无损降级批次隔离")
