from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_db_session
from app.modules.market.schemas import CheapLotsResponse
from app.modules.market.service import read_cheap_lots

router = APIRouter()


@router.get("/cheap-lots")
async def get_cheap_lots(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    min_discount_percent: Annotated[Decimal, Query(ge=0, le=95)] = Decimal("15"),
    min_sales_count: Annotated[int, Query(ge=1, le=1000)] = 3,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> CheapLotsResponse:
    """Возвращает дешёвые активные лоты относительно динамической исторической цены."""
    return await read_cheap_lots(
        session,
        min_discount_percent=min_discount_percent,
        min_sales_count=min_sales_count,
        limit=limit,
    )
