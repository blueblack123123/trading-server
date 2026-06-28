"""Add official API collection state.

Revision ID: 20260628_02
Revises: 20260628_01
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260628_02"
down_revision: str | None = "20260628_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "history_poll_states",
        sa.Column("backfill_offset", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "history_poll_states",
        sa.Column("backfill_complete", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "auction_lots",
        sa.Column("current_price", sa.Numeric(24, 4), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("auction_lots", "current_price")
    op.drop_column("history_poll_states", "backfill_complete")
    op.drop_column("history_poll_states", "backfill_offset")
