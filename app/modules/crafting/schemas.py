from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class CraftAnalysisResultResponse(BaseModel):
    item_id: str
    item_name: str | None
    category: str | None
    subcategory: str | None
    output_amount: int
    sell_price: Decimal | None
    craft_cost: Decimal | None
    direct_craft_cost: Decimal | None
    market_buy_price: Decimal | None
    profit: Decimal | None
    margin_percent: Decimal | None
    is_profitable: bool
    recommendation: str
    status: str
    ingredients: list[dict[str, Any]]
    recipe: dict[str, Any]


class CraftAnalysisResponse(BaseModel):
    run_id: int
    created_at: datetime
    period_hours: int
    min_margin_percent: Decimal
    recipes_count: int
    analyzed_count: int
    profitable_count: int
    missing_price_count: int
    results: list[CraftAnalysisResultResponse]
