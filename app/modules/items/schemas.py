from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MarketStatusName = Literal[
    "AUTO",
    "HOT",
    "NORMAL",
    "RARE",
    "IGNORE",
    "EXTREMELY_RARE",
]


class ItemStatusResponse(BaseModel):
    item_id: str
    name: str
    configured_status: MarketStatusName
    effective_status: MarketStatusName
    last_polled_at: datetime | None
    last_success_at: datetime | None
    next_poll_at: datetime | None
    consecutive_errors: int
