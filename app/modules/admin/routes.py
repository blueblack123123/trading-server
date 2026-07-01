from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_db_session
from app.core.config import settings
from app.modules.admin.schemas import (
    HistoryBackfillStatus,
    HistoryCollectionSettings,
    HistoryResolutionStorage,
    HistoryStatusResponse,
    HistoryStorageStatus,
    HistoryTopItemStorage,
    MarketItemConfig,
    MarketStatus,
)
from app.modules.admin.service import MarketItemsConfigService
from app.modules.history.models import (
    AuctionLot,
    AuctionSale,
    HistoryPollState,
    MarketItem,
    SaleAggregate,
)

router = APIRouter()


def check_admin_key(
    x_admin_key: str | None = Header(default=None),
) -> None:
    if not settings.admin_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_KEY is not configured",
        )

    if x_admin_key != settings.admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key",
        )


@router.get("/market-items-config")
async def get_market_items_config(
    _: None = Depends(check_admin_key),
) -> list[MarketItemConfig]:
    return MarketItemsConfigService().get_config()


@router.put("/market-items-config")
async def update_market_items_config(
    items: list[MarketItemConfig],
    _: None = Depends(check_admin_key),
) -> dict[str, int]:
    MarketItemsConfigService().save_config(items)

    return {
        "count": len(items),
    }


@router.post("/sync-market-items")
async def sync_market_items(
    _: None = Depends(check_admin_key),
) -> dict[str, int]:
    items = MarketItemsConfigService().sync_items()

    return {
        "count": len(items),
    }


@router.get("/history-status")
async def get_history_status(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: None = Depends(check_admin_key),
) -> HistoryStatusResponse:
    now = datetime.now(UTC)
    return HistoryStatusResponse(
        generated_at=now,
        settings=_history_collection_settings(),
        backfill=await _read_backfill_status(session, now),
        storage=await _read_storage_status(session),
    )


def _history_collection_settings() -> HistoryCollectionSettings:
    return HistoryCollectionSettings(
        live_requests_per_minute=settings.history_live_requests_per_minute,
        backfill_min_requests_per_minute=settings.history_backfill_requests_per_minute,
        backfill_max_requests_per_minute=settings.history_backfill_max_requests_per_minute,
        backfill_live_backlog_threshold=settings.history_backfill_live_backlog_threshold,
        raw_retention_hours=settings.history_raw_retention_hours,
        hourly_retention_hours=settings.history_hourly_retention_hours,
        max_raw_points_per_item=settings.history_max_raw_points_per_item,
        max_aggregate_points_per_item=settings.history_max_aggregate_points_per_item,
        compaction_interval_seconds=settings.history_compaction_interval_seconds,
    )


async def _read_backfill_status(session: AsyncSession, now: datetime) -> HistoryBackfillStatus:
    active_item_filter = (
        MarketItem.configured_status != int(MarketStatus.IGNORE),
        MarketItem.effective_status != int(MarketStatus.IGNORE),
    )
    tracked_items = int(
        await session.scalar(
            select(func.count()).select_from(MarketItem).where(*active_item_filter)
        )
        or 0
    )
    complete_items = int(
        await session.scalar(
            select(func.count())
            .select_from(MarketItem)
            .join(HistoryPollState, HistoryPollState.item_id == MarketItem.id)
            .where(*active_item_filter, HistoryPollState.backfill_complete.is_(True))
        )
        or 0
    )
    due_live_items = int(
        await session.scalar(
            select(func.count())
            .select_from(MarketItem)
            .join(HistoryPollState, HistoryPollState.item_id == MarketItem.id)
            .where(*active_item_filter, HistoryPollState.next_poll_at <= now)
        )
        or 0
    )
    aggregate_row = (
        await session.execute(
            select(
                func.min(HistoryPollState.backfill_next_at).label("oldest_next"),
                func.max(HistoryPollState.last_success_at).label("newest_success"),
                func.sum(HistoryPollState.backfill_offset).label("total_offset"),
                func.sum(HistoryPollState.backfill_target).label("total_target"),
            )
            .select_from(MarketItem)
            .join(HistoryPollState, HistoryPollState.item_id == MarketItem.id)
            .where(*active_item_filter)
        )
    ).one()
    return HistoryBackfillStatus(
        tracked_items=tracked_items,
        complete_items=complete_items,
        pending_items=max(0, tracked_items - complete_items),
        due_live_items=due_live_items,
        selected_backfill_requests_per_minute=_selected_backfill_rate(due_live_items),
        oldest_backfill_next_at=aggregate_row.oldest_next,
        newest_backfill_success_at=aggregate_row.newest_success,
        total_backfill_offset=int(aggregate_row.total_offset or 0),
        total_backfill_target=int(aggregate_row.total_target or 0),
    )


async def _read_storage_status(session: AsyncSession) -> HistoryStorageStatus:
    aggregate_resolutions = [
        HistoryResolutionStorage(
            resolution=str(row.resolution),
            points=int(row.points),
            items=int(row.items),
        )
        for row in (
            await session.execute(
                select(
                    SaleAggregate.resolution,
                    func.count().label("points"),
                    func.count(func.distinct(SaleAggregate.item_id)).label("items"),
                ).group_by(SaleAggregate.resolution)
            )
        )
    ]
    raw_points = int(await session.scalar(select(func.count()).select_from(AuctionSale)) or 0)
    aggregate_points = int(
        await session.scalar(select(func.count()).select_from(SaleAggregate)) or 0
    )
    active_lots = int(
        await session.scalar(
            select(func.count()).select_from(AuctionLot).where(AuctionLot.active.is_(True))
        )
        or 0
    )
    inactive_lots = int(
        await session.scalar(
            select(func.count()).select_from(AuctionLot).where(AuctionLot.active.is_(False))
        )
        or 0
    )
    item_storage = await _read_item_storage(session)
    return HistoryStorageStatus(
        raw_points=raw_points,
        aggregate_points=aggregate_points,
        active_lots=active_lots,
        inactive_lots=inactive_lots,
        database_size_bytes=await _read_database_size(session),
        aggregate_resolutions=aggregate_resolutions,
        top_items=item_storage,
        items=item_storage,
    )


async def _read_item_storage(session: AsyncSession) -> list[HistoryTopItemStorage]:
    raw_counts = (
        select(
            AuctionSale.item_id.label("item_id"),
            func.count().label("raw_points"),
        )
        .group_by(AuctionSale.item_id)
        .subquery()
    )
    aggregate_counts = (
        select(
            SaleAggregate.item_id.label("item_id"),
            func.count().label("aggregate_points"),
            func.count(func.distinct(SaleAggregate.additional_key)).label("aggregate_keys"),
        )
        .group_by(SaleAggregate.item_id)
        .subquery()
    )
    raw_points = func.coalesce(raw_counts.c.raw_points, 0)
    aggregate_points = func.coalesce(aggregate_counts.c.aggregate_points, 0)
    aggregate_keys = func.coalesce(aggregate_counts.c.aggregate_keys, 0)
    total_points = raw_points + aggregate_points
    rows = await session.execute(
        select(
            MarketItem.id,
            MarketItem.name,
            raw_points.label("raw_points"),
            aggregate_points.label("aggregate_points"),
            aggregate_keys.label("aggregate_keys"),
            total_points.label("total_points"),
        )
        .outerjoin(raw_counts, raw_counts.c.item_id == MarketItem.id)
        .outerjoin(aggregate_counts, aggregate_counts.c.item_id == MarketItem.id)
        .order_by(total_points.desc(), MarketItem.name)
    )
    return [
        HistoryTopItemStorage(
            item_id=str(row.id),
            name=str(row.name),
            raw_points=int(row.raw_points or 0),
            aggregate_points=int(row.aggregate_points or 0),
            aggregate_keys=int(row.aggregate_keys or 0),
            total_points=int(row.total_points or 0),
        )
        for row in rows
    ]


async def _read_database_size(session: AsyncSession) -> int | None:
    try:
        return int(
            await session.scalar(select(func.pg_database_size(func.current_database()))) or 0
        )
    except Exception:
        return None


def _selected_backfill_rate(due_live_items: int) -> int:
    minimum = settings.history_backfill_requests_per_minute
    maximum = settings.history_backfill_max_requests_per_minute
    if maximum <= minimum:
        return minimum
    if due_live_items >= settings.history_backfill_live_backlog_threshold:
        return minimum
    return maximum
