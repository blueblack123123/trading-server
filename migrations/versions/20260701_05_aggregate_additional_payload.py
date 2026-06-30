"""Store additional payloads in sale aggregates.

Revision ID: 20260701_05
Revises: 20260629_04
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260701_05"
down_revision: str | None = "20260629_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EMPTY_ADDITIONAL_KEY = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


def upgrade() -> None:
    op.add_column(
        "sale_aggregates",
        sa.Column(
            "additional",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "sale_aggregates",
        sa.Column(
            "additional_key",
            sa.String(length=64),
            nullable=False,
            server_default=EMPTY_ADDITIONAL_KEY,
        ),
    )
    op.drop_constraint("uq_sale_aggregate_bucket", "sale_aggregates", type_="unique")
    op.create_unique_constraint(
        "uq_sale_aggregate_bucket",
        "sale_aggregates",
        ["item_id", "resolution", "bucket_start", "quality_key", "additional_key"],
    )
    op.execute(sa.text("DELETE FROM sale_aggregates"))
    op.alter_column("sale_aggregates", "additional", server_default=None)
    op.alter_column("sale_aggregates", "additional_key", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_sale_aggregate_bucket", "sale_aggregates", type_="unique")
    op.create_unique_constraint(
        "uq_sale_aggregate_bucket",
        "sale_aggregates",
        ["item_id", "resolution", "bucket_start", "quality_key"],
    )
    op.drop_column("sale_aggregates", "additional_key")
    op.drop_column("sale_aggregates", "additional")
