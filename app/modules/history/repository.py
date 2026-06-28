from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.modules.history.domain import LotRecord, SaleRecord
from app.modules.history.models import AuctionLot, AuctionSale, SaleAggregate

UNKNOWN_QUALITY_KEY = -1


async def store_sales(session: AsyncSession, records: Sequence[SaleRecord]) -> int:
    if not records:
        return 0

    statement = (
        insert(AuctionSale)
        .values(
            [
                {
                    "item_id": record.item_id,
                    "sold_at": record.sold_at,
                    "amount": record.amount,
                    "price": record.price,
                    "quality": record.quality,
                    "additional": record.additional,
                    "fingerprint": record.fingerprint,
                }
                for record in records
            ]
        )
        .on_conflict_do_nothing(index_elements=[AuctionSale.fingerprint])
        .returning(
            AuctionSale.item_id,
            AuctionSale.sold_at,
            AuctionSale.amount,
            AuctionSale.price,
            AuctionSale.quality,
        )
    )
    result = await session.execute(statement)
    inserted = list(result.mappings())

    await _increment_aggregates(session, inserted)
    return len(inserted)


async def store_lots(
    session: AsyncSession,
    item_id: str,
    records: Sequence[LotRecord],
    observed_at: datetime,
    previous_observed_at: datetime | None,
    complete_snapshot: bool,
) -> tuple[int, int]:
    existing_query = select(AuctionLot.fingerprint).where(AuctionLot.item_id == item_id)
    if complete_snapshot:
        existing_query = existing_query.where(AuctionLot.active.is_(True))
    elif previous_observed_at is not None:
        existing_query = existing_query.where(AuctionLot.last_seen_at == previous_observed_at)
    else:
        existing_query = existing_query.where(AuctionLot.fingerprint == "")
    existing_result = await session.scalars(existing_query)
    existing = set(existing_result.all())
    current = {record.fingerprint for record in records}

    if records:
        statement = insert(AuctionLot).values(
            [
                {
                    "fingerprint": record.fingerprint,
                    "item_id": record.item_id,
                    "amount": record.amount,
                    "start_price": record.start_price,
                    "buyout_price": record.buyout_price,
                    "start_time": record.start_time,
                    "end_time": record.end_time,
                    "quality": record.quality,
                    "additional": record.additional,
                    "first_seen_at": observed_at,
                    "last_seen_at": observed_at,
                    "active": True,
                }
                for record in records
            ]
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[AuctionLot.fingerprint],
                set_={
                    "last_seen_at": observed_at,
                    "disappeared_at": None,
                    "active": True,
                    "updated_at": func.now(),
                },
            )
        )

    disappeared = existing - current if complete_snapshot else set()
    if disappeared:
        await session.execute(
            update(AuctionLot)
            .where(AuctionLot.fingerprint.in_(disappeared))
            .values(active=False, disappeared_at=observed_at)
        )

    return len(current - existing), len(disappeared)


async def _increment_aggregates(
    session: AsyncSession,
    rows: Sequence[RowMapping],
) -> None:
    grouped: dict[tuple[str, str, datetime, int | None], dict[str, Any]] = defaultdict(
        lambda: {
            "min_price": None,
            "max_price": None,
            "price_sum": Decimal(0),
            "weighted_price_sum": Decimal(0),
            "amount_sum": 0,
            "sale_count": 0,
        }
    )

    for row in rows:
        sold_at = row["sold_at"]
        assert isinstance(sold_at, datetime)
        price = Decimal(row["price"])
        amount = int(row["amount"])
        quality = row["quality"]
        assert quality is None or isinstance(quality, int)

        for resolution, bucket in (
            ("hour", sold_at.replace(minute=0, second=0, microsecond=0)),
            ("day", sold_at.replace(hour=0, minute=0, second=0, microsecond=0)),
        ):
            key = (str(row["item_id"]), resolution, bucket, quality)
            values = grouped[key]
            current_min = values["min_price"]
            current_max = values["max_price"]
            values["min_price"] = price if current_min is None else min(current_min, price)
            values["max_price"] = price if current_max is None else max(current_max, price)
            values["price_sum"] += price
            values["weighted_price_sum"] += price * amount
            values["amount_sum"] += amount
            values["sale_count"] += 1

    for (item_id, resolution, bucket, quality), values in grouped.items():
        aggregate_insert = insert(SaleAggregate).values(
            item_id=item_id,
            resolution=resolution,
            bucket_start=bucket,
            quality=quality,
            quality_key=UNKNOWN_QUALITY_KEY if quality is None else quality,
            **values,
        )
        await session.execute(
            aggregate_insert.on_conflict_do_update(
                constraint="uq_sale_aggregate_bucket",
                set_={
                    "min_price": func.least(
                        SaleAggregate.min_price,
                        aggregate_insert.excluded.min_price,
                    ),
                    "max_price": func.greatest(
                        SaleAggregate.max_price,
                        aggregate_insert.excluded.max_price,
                    ),
                    "price_sum": SaleAggregate.price_sum + aggregate_insert.excluded.price_sum,
                    "weighted_price_sum": SaleAggregate.weighted_price_sum
                    + aggregate_insert.excluded.weighted_price_sum,
                    "amount_sum": SaleAggregate.amount_sum + aggregate_insert.excluded.amount_sum,
                    "sale_count": SaleAggregate.sale_count + aggregate_insert.excluded.sale_count,
                    "updated_at": func.now(),
                },
            )
        )


async def compact_history(
    session: AsyncSession,
    now: datetime | None = None,
) -> tuple[int, int, int]:
    current = now or datetime.now(UTC)
    raw_result = await session.execute(
        delete(AuctionSale).where(AuctionSale.sold_at < current - timedelta(hours=48))
    )
    hourly_result = await session.execute(
        delete(SaleAggregate).where(
            SaleAggregate.resolution == "hour",
            SaleAggregate.bucket_start < current - timedelta(days=35),
        )
    )
    lots_result = await session.execute(
        delete(AuctionLot).where(
            AuctionLot.active.is_(False),
            AuctionLot.disappeared_at < current - timedelta(hours=48),
        )
    )
    raw_count = int(getattr(raw_result, "rowcount", 0) or 0)
    hourly_count = int(getattr(hourly_result, "rowcount", 0) or 0)
    lots_count = int(getattr(lots_result, "rowcount", 0) or 0)
    return raw_count, hourly_count, lots_count


async def get_active_lots(
    session: AsyncSession,
    item_id: str,
    quality: int | None,
    observed_at: datetime | None,
) -> Sequence[AuctionLot]:
    if observed_at is None:
        return []
    query = select(AuctionLot).where(
        AuctionLot.item_id == item_id,
        AuctionLot.last_seen_at == observed_at,
    )
    if quality is not None:
        query = query.where(AuctionLot.quality == quality)
    result = await session.scalars(query.order_by(AuctionLot.buyout_price, AuctionLot.end_time))
    return result.all()


async def get_raw_sales(
    session: AsyncSession,
    item_id: str,
    start: datetime,
    end: datetime,
    quality: int | None,
) -> Sequence[AuctionSale]:
    query = select(AuctionSale).where(
        AuctionSale.item_id == item_id,
        AuctionSale.sold_at >= start,
        AuctionSale.sold_at < end,
    )
    if quality is not None:
        query = query.where(AuctionSale.quality == quality)
    result = await session.scalars(query.order_by(AuctionSale.sold_at))
    return result.all()


async def get_aggregates(
    session: AsyncSession,
    item_id: str,
    resolution: str,
    start: datetime,
    end: datetime,
    quality: int | None,
) -> Sequence[SaleAggregate]:
    query = select(SaleAggregate).where(
        SaleAggregate.item_id == item_id,
        SaleAggregate.resolution == resolution,
        SaleAggregate.bucket_start >= start,
        SaleAggregate.bucket_start < end,
    )
    if quality is not None:
        query = query.where(SaleAggregate.quality == quality)
    result = await session.scalars(query.order_by(SaleAggregate.bucket_start))
    return result.all()
