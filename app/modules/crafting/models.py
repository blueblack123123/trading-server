from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CraftAnalysisRun(Base):
    __tablename__ = "craft_analysis_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    period_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    min_margin_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    recipes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analyzed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profitable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_price_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CraftAnalysisResult(Base):
    __tablename__ = "craft_analysis_results"
    __table_args__ = (
        Index("ix_craft_analysis_results_run_margin", "run_id", "margin_percent"),
        Index("ix_craft_analysis_results_run_profitable", "run_id", "is_profitable"),
        Index("ix_craft_analysis_results_item", "item_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("craft_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_name: Mapped[str | None] = mapped_column(String(512))
    category: Mapped[str | None] = mapped_column(String(256))
    subcategory: Mapped[str | None] = mapped_column(String(256))
    output_amount: Mapped[int] = mapped_column(Integer, nullable=False)

    sell_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    craft_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    direct_craft_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    market_buy_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    profit: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    margin_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    market_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    market_sale_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    liquidity_score: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    liquidity_status: Mapped[str] = mapped_column(String(32), nullable=False, default="none")

    is_profitable: Mapped[bool] = mapped_column(nullable=False, default=False)
    recommendation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    ingredients: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    recipe: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
