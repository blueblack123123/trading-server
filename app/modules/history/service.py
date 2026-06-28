from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.stalzone import StalzoneClient
from app.core.config import settings
from app.modules.history.domain import LotRecord, parse_lot
from app.modules.history.models import LotPollState, MarketItem
from app.modules.history.repository import (
    get_active_lots,
    get_aggregates,
    get_raw_sales,
    store_lots,
)
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
) -> ActiveLotsResponse:
    state = await session.get(LotPollState, item_id)
    lots = await get_active_lots(
        session,
        item_id,
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
                item_id=lot.item_id,
                amount=lot.amount,
                start_price=lot.start_price,
                current_price=lot.current_price,
                buyout_price=lot.buyout_price,
                start_time=lot.start_time,
                end_time=lot.end_time,
                quality=lot.quality,
                additional=lot.additional,
                first_seen_at=lot.first_seen_at,
                last_seen_at=lot.last_seen_at,
            )
            for lot in lots
        ],
    )


async def get_or_refresh_active_lots(
    session: AsyncSession,
    item_id: str,
) -> ActiveLotsResponse:
    item = await session.get(MarketItem, item_id)
    if item is None:
        raise LookupError("item is not configured")

    state = await session.get(LotPollState, item_id)
    now = datetime.now(UTC)
    if (
        state is not None
        and state.last_success_at is not None
        and state.last_success_at >= now - timedelta(seconds=settings.lots_cache_ttl_seconds)
    ):
        return await read_active_lots(session, item_id)

    records, total, complete = await _fetch_all_lots(item_id)
    if state is None:
        state = LotPollState(item_id=item_id, next_poll_at=now)
        session.add(state)
        await session.flush()

    await store_lots(
        session,
        item_id,
        records,
        now,
        state.last_success_at,
        complete,
    )
    state.last_polled_at = now
    state.last_success_at = now
    state.last_http_status = 200
    state.consecutive_errors = 0
    state.total_lots = total
    state.snapshot_complete = complete
    await session.commit()
    return await read_active_lots(session, item_id)


async def _fetch_all_lots(item_id: str) -> tuple[list[LotRecord], int, bool]:
    records_by_fingerprint: dict[str, LotRecord] = {}
    offset = 0
    total = 0
    async with StalzoneClient() as client:
        while offset < total or offset == 0:
            payload = await client.get_available_lots(
                item_id=item_id,
                limit=200,
                offset=offset,
            )
            page, total = _parse_lots_response(item_id, payload)
            records_by_fingerprint.update((record.fingerprint, record) for record in page)
            offset += len(page)
            if offset >= total:
                break
            if not page:
                raise RuntimeError("Stalzone API returned an incomplete lots listing")
    if len(records_by_fingerprint) != total:
        raise RuntimeError("Stalzone API returned duplicate or incomplete lots")
    return list(records_by_fingerprint.values()), total, True


def _parse_lots_response(item_id: str, payload: Any) -> tuple[list[LotRecord], int]:
    if not isinstance(payload, dict):
        raise ValueError("lots response must be an object")
    raw_lots = payload.get("lots", [])
    if not isinstance(raw_lots, list):
        raise ValueError("lots response lots must be a list")
    records = [parse_lot(item_id, row) for row in raw_lots if isinstance(row, dict)]
    raw_total = payload.get("total", len(records))
    total = int(raw_total) if isinstance(raw_total, (int, float, str)) else len(records)
    return records, max(total, len(records))
