"""Add craft liquidity metrics.

Revision ID: 20260928_08
Revises: 20260927_07
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_08"
down_revision: str | None = "20260927_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "craft_analysis_results",
        sa.Column("market_amount", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "craft_analysis_results",
        sa.Column("market_sale_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "craft_analysis_results",
        sa.Column(
            "liquidity_score",
            sa.Numeric(12, 4),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "craft_analysis_results",
        sa.Column(
            "liquidity_status",
            sa.String(length=32),
            server_default="none",
            nullable=False,
        ),
    )
    op.alter_column("craft_analysis_results", "market_amount", server_default=None)
    op.alter_column("craft_analysis_results", "market_sale_count", server_default=None)
    op.alter_column("craft_analysis_results", "liquidity_score", server_default=None)
    op.alter_column("craft_analysis_results", "liquidity_status", server_default=None)


def downgrade() -> None:
    op.drop_column("craft_analysis_results", "liquidity_status")
    op.drop_column("craft_analysis_results", "liquidity_score")
    op.drop_column("craft_analysis_results", "market_sale_count")
    op.drop_column("craft_analysis_results", "market_amount")
