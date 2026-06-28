from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_db_session
from app.modules.history.schemas import ActiveLotsResponse, ItemHistoryResponse
from app.modules.history.service import get_or_refresh_active_lots, read_history

router = APIRouter()


@router.get("/{item_id}/history")
async def get_item_history(
    item_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: datetime | None = None,
    resolution: Literal["auto", "raw", "hour", "day"] = "auto",
    qlt: Annotated[int | None, Query(ge=0, le=255)] = None,
) -> ItemHistoryResponse:
    end = to_time or datetime.now(UTC)
    start = from_time or end - timedelta(hours=24)
    try:
        return await read_history(
            session=session,
            item_id=item_id,
            start=start,
            end=end,
            quality=qlt,
            resolution=resolution,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.get("/{item_id}/lots")
async def get_item_lots(
    item_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ActiveLotsResponse:
    try:
        return await get_or_refresh_active_lots(session, item_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        response_status = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if upstream_status == status.HTTP_429_TOO_MANY_REQUESTS
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(
            status_code=response_status,
            detail=f"Stalzone API returned HTTP {upstream_status}",
        ) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
