from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class CheapLot(BaseModel):
    item_id: str
    item_name: str
    fingerprint: str
    amount: int
    buyout_price: Decimal
    unit_price: Decimal
    fair_unit_price: Decimal
    discount_percent: Decimal
    expected_profit: Decimal
    expected_profit_percent: Decimal
    sales_count: int
    amount_sold: int
    period_hours: int
    quality: int | None
    additional: dict[str, Any]
    end_time: datetime
    last_seen_at: datetime


class CheapLotsResponse(BaseModel):
    generated_at: datetime
    min_discount_percent: Decimal
    min_sales_count: int
    total: int
    lots: list[CheapLot]
