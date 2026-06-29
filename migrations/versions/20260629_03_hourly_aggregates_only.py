"""Keep only permanent hourly sale aggregates.

Revision ID: 20260629_03
Revises: 20260628_02
Create Date: 2026-06-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260629_03"
down_revision: str | None = "20260628_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM sale_aggregates WHERE resolution = 'day'"))


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO sale_aggregates (
                item_id,
                resolution,
                bucket_start,
                quality,
                quality_key,
                min_price,
                max_price,
                price_sum,
                weighted_price_sum,
                amount_sum,
                sale_count,
                created_at,
                updated_at
            )
            SELECT
                item_id,
                'day',
                date_trunc('day', bucket_start AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
                quality,
                quality_key,
                min(min_price),
                max(max_price),
                sum(price_sum),
                sum(weighted_price_sum),
                sum(amount_sum),
                sum(sale_count),
                now(),
                now()
            FROM sale_aggregates
            WHERE resolution = 'hour'
            GROUP BY
                item_id,
                date_trunc('day', bucket_start AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
                quality,
                quality_key
            """
        )
    )
