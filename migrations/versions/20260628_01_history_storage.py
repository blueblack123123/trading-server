"""Add auction history storage.

Revision ID: 20260628_01
Revises:
Create Date: 2026-06-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260628_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("configured_status", sa.SmallInteger(), nullable=False),
        sa.Column("effective_status", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "history_poll_states",
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column(
            "next_poll_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_sale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "latest_sale_fingerprints",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("consecutive_errors", sa.Integer(), nullable=False),
        sa.Column("activity_score", sa.Float(), nullable=False),
        sa.Column("auto_candidate_status", sa.SmallInteger(), nullable=True),
        sa.Column("auto_candidate_runs", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["item_id"], ["market_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index(
        "ix_history_poll_states_next_poll_at",
        "history_poll_states",
        ["next_poll_at"],
    )
    op.create_table(
        "lot_poll_states",
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column(
            "next_poll_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("consecutive_errors", sa.Integer(), nullable=False),
        sa.Column("activity_score", sa.Float(), nullable=False),
        sa.Column("total_lots", sa.Integer(), nullable=False),
        sa.Column("snapshot_complete", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["item_id"], ["market_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index("ix_lot_poll_states_next_poll_at", "lot_poll_states", ["next_poll_at"])
    op.create_table(
        "auction_sales",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("quality", sa.SmallInteger(), nullable=True),
        sa.Column("additional", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["item_id"], ["market_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_index(
        "ix_auction_sales_item_time_quality",
        "auction_sales",
        ["item_id", "sold_at", "quality"],
    )
    op.create_table(
        "auction_lots",
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("start_price", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("buyout_price", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.SmallInteger(), nullable=True),
        sa.Column("additional", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disappeared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["item_id"], ["market_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fingerprint"),
    )
    op.create_index(
        "ix_auction_lots_item_active_quality",
        "auction_lots",
        ["item_id", "active", "quality"],
    )
    op.create_table(
        "sale_aggregates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("resolution", sa.String(length=8), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.SmallInteger(), nullable=True),
        sa.Column("quality_key", sa.SmallInteger(), nullable=False),
        sa.Column("min_price", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("max_price", sa.Numeric(precision=24, scale=4), nullable=False),
        sa.Column("price_sum", sa.Numeric(precision=32, scale=4), nullable=False),
        sa.Column("weighted_price_sum", sa.Numeric(precision=36, scale=4), nullable=False),
        sa.Column("amount_sum", sa.BigInteger(), nullable=False),
        sa.Column("sale_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["item_id"], ["market_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "resolution",
            "bucket_start",
            "quality_key",
            name="uq_sale_aggregate_bucket",
        ),
    )
    op.create_index(
        "ix_sale_aggregates_item_resolution_time",
        "sale_aggregates",
        ["item_id", "resolution", "bucket_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_sale_aggregates_item_resolution_time", table_name="sale_aggregates")
    op.drop_table("sale_aggregates")
    op.drop_index("ix_auction_sales_item_time_quality", table_name="auction_sales")
    op.drop_table("auction_sales")
    op.drop_index("ix_auction_lots_item_active_quality", table_name="auction_lots")
    op.drop_table("auction_lots")
    op.drop_index("ix_lot_poll_states_next_poll_at", table_name="lot_poll_states")
    op.drop_table("lot_poll_states")
    op.drop_index("ix_history_poll_states_next_poll_at", table_name="history_poll_states")
    op.drop_table("history_poll_states")
    op.drop_table("market_items")
