"""add versioned P1 features

Revision ID: 4e9a25af5c18
Revises: b884b2856227
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4e9a25af5c18"
down_revision: str | None = "b884b2856227"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "screener_preset",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "decision_note",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.add_column(
        "decision_note",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_active_decision_note",
        "decision_note",
        ["market", "stock_code", "trade_date"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "rule_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(32), nullable=False, unique=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("requires_recalculation", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "alert_rule_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("logical_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("rule_code", sa.String(64), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("logical_id", "version"),
    )
    op.create_index(
        "ix_alert_rule_version_logical_id",
        "alert_rule_version",
        ["logical_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_rule_version_logical_id", table_name="alert_rule_version")
    op.drop_table("alert_rule_version")
    op.drop_table("rule_version")
    op.drop_index(
        "uq_active_decision_note",
        table_name="decision_note",
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_column("decision_note", "deleted_at")
    op.drop_column("decision_note", "updated_at")
    op.drop_column("screener_preset", "is_default")
