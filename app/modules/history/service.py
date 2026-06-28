from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.history.models import LotPollState
from app.modules.history.repository import get_active_lots, get_aggregates, get_raw_sales
from app.modules.history.schemas import (
    ActiveLot,
    ActiveLotsResponse,
    HistoryPoint,
    ItemHistoryResponse,
)


async def read_history(
    session: AsyncSession,
    item_id: str,
    start: datetime,
    end: datetime,
    quality: int | None,
    resolution: str,
) -> ItemHistoryResponse:
    start = _as_utc(start)
    end = _as_utc(end)
    if start >= end:
        raise ValueError("from must be earlier than to")

    points: list[HistoryPoint] = []
    if resolution == "auto":
        now = datetime.now(UTC)
        day_boundary = (now - timedelta(days=30)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        raw_boundary = (now - timedelta(hours=24)).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        await _append_aggregate_points(
            points, session, item_id, "day", start, min(end, day_boundary), quality
        )
        await _append_aggregate_points(
            points,
            session,
            item_id,
            "hour",
            max(start, day_boundary),
            min(end, raw_boundary),
            quality,
        )
        await _append_raw_points(points, session, item_id, max(start, raw_boundary), end, quality)
    elif resolution == "raw":
        await _append_raw_points(points, session, item_id, start, end, quality)
    elif resolution in {"hour", "day"}:
        await _append_aggregate_points(points, session, item_id, resolution, start, end, quality)
    else:
        raise ValueError("resolution must be auto, raw, hour or day")

    points.sort(key=lambda point: point.timestamp)
    return ItemHistoryResponse(item_id=item_id, from_=start, to=end, points=points)


async def _append_raw_points(
    points: list[HistoryPoint],
    session: AsyncSession,
    item_id: str,
    start: datetime,
    end: datetime,
    quality: int | None,
) -> None:
    if start >= end:
        return
    for sale in await get_raw_sales(session, item_id, start, end, quality):
        points.append(
            HistoryPoint(
                timestamp=sale.sold_at,
                resolution="raw",
                quality=sale.quality,
                min_price=sale.price,
                max_price=sale.price,
                average_price=sale.price,
                weighted_average_price=sale.price,
                amount=sale.amount,
                sale_count=1,
            )
        )


async def _append_aggregate_points(
    points: list[HistoryPoint],
    session: AsyncSession,
    item_id: str,
    resolution: str,
    start: datetime,
    end: datetime,
    quality: int | None,
) -> None:
    if start >= end:
        return
    aggregates = await get_aggregates(session, item_id, resolution, start, end, quality)
    for aggregate in aggregates:
        sale_count = int(aggregate.sale_count)
        amount_sum = int(aggregate.amount_sum)
        points.append(
            HistoryPoint(
                timestamp=aggregate.bucket_start,
                resolution=resolution,  # type: ignore[arg-type]
                quality=aggregate.quality,
                min_price=aggregate.min_price,
                max_price=aggregate.max_price,
                average_price=aggregate.price_sum / Decimal(sale_count),
                weighted_average_price=aggregate.weighted_price_sum / Decimal(amount_sum),
                amount=amount_sum,
                sale_count=sale_count,
            )
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def read_active_lots(
    session: AsyncSession,
    item_id: str,
    quality: int | None,
) -> ActiveLotsResponse:
    state = await session.get(LotPollState, item_id)
    lots = await get_active_lots(
        session,
        item_id,
        quality,
        state.last_success_at if state is not None else None,
    )
    return ActiveLotsResponse(
        item_id=item_id,
        updated_at=state.last_success_at if state is not None else None,
        total=state.total_lots if state is not None else 0,
        snapshot_complete=state.snapshot_complete if state is not None else False,
        lots=[
            ActiveLot(
                fingerprint=lot.fingerprint,
                amount=lot.amount,
                start_price=lot.start_price,
                buyout_price=lot.buyout_price,
                start_time=lot.start_time,
                end_time=lot.end_time,
                quality=lot.quality,
                first_seen_at=lot.first_seen_at,
                last_seen_at=lot.last_seen_at,
            )
            for lot in lots
        ],
    )
