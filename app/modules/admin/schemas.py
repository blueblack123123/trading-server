from pydantic import BaseModel, Field


class MarketItemConfig(BaseModel):
    id: str
    name: str
    status: int = Field(ge=0, le=4)