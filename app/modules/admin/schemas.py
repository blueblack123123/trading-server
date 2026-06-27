from enum import IntEnum

from pydantic import BaseModel


class MarketStatus(IntEnum):
    AUTO = 0
    HOT = 1
    NORMAL = 2
    RARE = 3
    IGNORE = 4


class MarketItemConfig(BaseModel):
    id: str
    name: str
    status: MarketStatus = MarketStatus.AUTO