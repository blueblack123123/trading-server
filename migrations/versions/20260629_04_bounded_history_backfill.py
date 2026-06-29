"""Add bounded background history backfill state.

Revision ID: 20260629_04
Revises: 20260629_03
Create Date: 2026-06-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260629_04"
down_revision: str | None = "20260629_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "history_poll_states",
        sa.Column("backfill_target", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "history_poll_states",
        sa.Column(
            "backfill_next_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_history_poll_states_backfill_next_at",
        "history_poll_states",
        ["backfill_next_at"],
    )
    op.execute(
        sa.text(
            """
            UPDATE history_poll_states
            SET backfill_offset = 0,
                backfill_target = 0,
                backfill_complete = false,
                backfill_next_at = now()
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_history_poll_states_backfill_next_at",
        table_name="history_poll_states",
    )
    op.drop_column("history_poll_states", "backfill_next_at")
    op.drop_column("history_poll_states", "backfill_target")
