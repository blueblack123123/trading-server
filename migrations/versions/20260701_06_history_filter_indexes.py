"""Add indexes for history additional filters.

Revision ID: 20260701_06
Revises: 20260701_05
Create Date: 2026-07-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260701_06"
down_revision: str | None = "20260701_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_auction_sales_additional_gin
        ON auction_sales USING gin (additional jsonb_path_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sale_aggregates_additional_gin
        ON sale_aggregates USING gin (additional jsonb_path_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sale_aggregates_item_additional_key
        ON sale_aggregates (item_id, additional_key)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sale_aggregates_item_additional_key")
    op.execute("DROP INDEX IF EXISTS ix_sale_aggregates_additional_gin")
    op.execute("DROP INDEX IF EXISTS ix_auction_sales_additional_gin")
