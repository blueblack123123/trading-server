from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HistoryPoint(BaseModel):
    timestamp: datetime
    resolution: Literal["raw", "hour", "day"]
    quality: int | None
    min_price: Decimal
    max_price: Decimal
    average_price: Decimal
    weighted_average_price: Decimal
    amount: int
    sale_count: int


class ItemHistoryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_id: str
    from_: datetime = Field(serialization_alias="from")
    to: datetime
    points: list[HistoryPoint]


class ActiveLot(BaseModel):
    fingerprint: str
    amount: int
    start_price: Decimal
    current_price: Decimal
    buyout_price: Decimal
    start_time: datetime
    end_time: datetime
    quality: int | None
    first_seen_at: datetime
    last_seen_at: datetime


class ActiveLotsResponse(BaseModel):
    item_id: str
    updated_at: datetime | None
    total: int
    snapshot_complete: bool
    lots: list[ActiveLot]
