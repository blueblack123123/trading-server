"""Add craft analysis reports.

Revision ID: 20260927_07
Revises: 20260701_06
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260927_07"
down_revision: str | None = "20260701_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "craft_analysis_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("period_hours", sa.Integer(), nullable=False),
        sa.Column("min_margin_percent", sa.Numeric(10, 4), nullable=False),
        sa.Column("recipes_count", sa.Integer(), nullable=False),
        sa.Column("analyzed_count", sa.Integer(), nullable=False),
        sa.Column("profitable_count", sa.Integer(), nullable=False),
        sa.Column("missing_price_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "craft_analysis_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("item_name", sa.String(length=512), nullable=True),
        sa.Column("category", sa.String(length=256), nullable=True),
        sa.Column("subcategory", sa.String(length=256), nullable=True),
        sa.Column("output_amount", sa.Integer(), nullable=False),
        sa.Column("sell_price", sa.Numeric(24, 4), nullable=True),
        sa.Column("craft_cost", sa.Numeric(24, 4), nullable=True),
        sa.Column("direct_craft_cost", sa.Numeric(24, 4), nullable=True),
        sa.Column("market_buy_price", sa.Numeric(24, 4), nullable=True),
        sa.Column("profit", sa.Numeric(24, 4), nullable=True),
        sa.Column("margin_percent", sa.Numeric(12, 4), nullable=True),
        sa.Column("is_profitable", sa.Boolean(), nullable=False),
        sa.Column("recommendation", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "ingredients",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("recipe", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["craft_analysis_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_craft_analysis_results_item",
        "craft_analysis_results",
        ["item_id"],
        unique=False,
    )
    op.create_index(
        "ix_craft_analysis_results_run_margin",
        "craft_analysis_results",
        ["run_id", "margin_percent"],
        unique=False,
    )
    op.create_index(
        "ix_craft_analysis_results_run_profitable",
        "craft_analysis_results",
        ["run_id", "is_profitable"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_craft_analysis_results_run_profitable",
        table_name="craft_analysis_results",
    )
    op.drop_index(
        "ix_craft_analysis_results_run_margin",
        table_name="craft_analysis_results",
    )
    op.drop_index("ix_craft_analysis_results_item", table_name="craft_analysis_results")
    op.drop_table("craft_analysis_results")
    op.drop_table("craft_analysis_runs")
