from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class MarketItem(TimestampMixin, Base):
    __tablename__ = "market_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    configured_status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    effective_status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)


class HistoryPollState(TimestampMixin, Base):
    __tablename__ = "history_poll_states"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("market_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_sale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_sale_fingerprints: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    backfill_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backfill_complete: Mapped[bool] = mapped_column(nullable=False, default=False)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    consecutive_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activity_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    auto_candidate_status: Mapped[int | None] = mapped_column(SmallInteger)
    auto_candidate_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class LotPollState(TimestampMixin, Base):
    __tablename__ = "lot_poll_states"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("market_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    consecutive_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activity_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_lots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_complete: Mapped[bool] = mapped_column(nullable=False, default=False)


class AuctionSale(TimestampMixin, Base):
    __tablename__ = "auction_sales"
    __table_args__ = (Index("ix_auction_sales_item_time_quality", "item_id", "sold_at", "quality"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("market_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    quality: Mapped[int | None] = mapped_column(SmallInteger)
    additional: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class AuctionLot(TimestampMixin, Base):
    __tablename__ = "auction_lots"
    __table_args__ = (Index("ix_auction_lots_item_active_quality", "item_id", "active", "quality"),)

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("market_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    start_price: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    buyout_price: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality: Mapped[int | None] = mapped_column(SmallInteger)
    additional: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disappeared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class SaleAggregate(TimestampMixin, Base):
    __tablename__ = "sale_aggregates"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "resolution",
            "bucket_start",
            "quality_key",
            name="uq_sale_aggregate_bucket",
        ),
        Index(
            "ix_sale_aggregates_item_resolution_time",
            "item_id",
            "resolution",
            "bucket_start",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("market_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    resolution: Mapped[str] = mapped_column(String(8), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality: Mapped[int | None] = mapped_column(SmallInteger)
    quality_key: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    min_price: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    max_price: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    price_sum: Mapped[Decimal] = mapped_column(Numeric(32, 4), nullable=False)
    weighted_price_sum: Mapped[Decimal] = mapped_column(Numeric(36, 4), nullable=False)
    amount_sum: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sale_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
