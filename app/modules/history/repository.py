import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, delete, or_, select, update
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

    inserted_count = 0
    for start in range(0, len(records), 500):
        chunk = records[start : start + 500]
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
                    for record in chunk
                ]
            )
            .on_conflict_do_nothing(index_elements=[AuctionSale.fingerprint])
            .returning(
                AuctionSale.item_id,
                AuctionSale.sold_at,
                AuctionSale.amount,
                AuctionSale.price,
                AuctionSale.quality,
                AuctionSale.additional,
            )
        )
        result = await session.execute(statement)
        inserted = list(result.mappings())
        await _increment_aggregates(session, inserted)
        inserted_count += len(inserted)

    return inserted_count


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
        for start in range(0, len(records), 500):
            chunk = records[start : start + 500]
            statement = insert(AuctionLot).values(
                [
                    {
                        "fingerprint": record.fingerprint,
                        "item_id": record.item_id,
                        "amount": record.amount,
                        "start_price": record.start_price,
                        "current_price": record.current_price,
                        "buyout_price": record.buyout_price,
                        "start_time": record.start_time,
                        "end_time": record.end_time,
                        "quality": record.quality,
                        "additional": record.additional,
                        "first_seen_at": observed_at,
                        "last_seen_at": observed_at,
                        "active": True,
                    }
                    for record in chunk
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
    values = _group_aggregate_values(
        (
            (
                str(row["item_id"]),
                row["sold_at"],
                int(row["amount"]),
                Decimal(row["price"]),
                row["quality"],
                row["additional"],
            )
            for row in rows
        ),
        resolution="hour",
    )
    await _upsert_aggregate_values(session, values, additive=True)


async def replace_hourly_aggregates(
    session: AsyncSession,
    records: Sequence[SaleRecord],
) -> int:
    """Idempotently replaces complete hourly buckets collected by backfill."""
    values = _group_aggregate_values(
        (
            (
                record.item_id,
                record.sold_at,
                record.amount,
                record.price,
                record.quality,
                record.additional,
            )
            for record in records
        ),
        resolution="hour",
    )
    await _upsert_aggregate_values(session, values, additive=False)
    return len(values)


async def replace_daily_aggregates(
    session: AsyncSession,
    records: Sequence[SaleRecord],
) -> int:
    """Idempotently replaces complete daily buckets collected by backfill."""
    values = _group_aggregate_values(
        (
            (
                record.item_id,
                record.sold_at,
                record.amount,
                record.price,
                record.quality,
                record.additional,
            )
            for record in records
        ),
        resolution="day",
    )
    await _upsert_aggregate_values(session, values, additive=False)
    return len(values)


def _group_aggregate_values(
    rows: Iterable[tuple[str, datetime, int, Decimal, int | None, dict[str, Any]]],
    *,
    resolution: str,
) -> list[dict[str, Any]]:
    if resolution not in {"20min", "hour", "day"}:
        raise ValueError("resolution must be 20min, hour or day")
    grouped: dict[tuple[str, datetime, int | None, str], dict[str, Any]] = defaultdict(
        lambda: {
            "min_price": None,
            "max_price": None,
            "price_sum": Decimal(0),
            "weighted_price_sum": Decimal(0),
            "amount_sum": 0,
            "sale_count": 0,
        }
    )

    for item_id, sold_at, amount, price, quality, additional in rows:
        if resolution == "day":
            bucket = sold_at.replace(hour=0, minute=0, second=0, microsecond=0)
        elif resolution == "20min":
            bucket = sold_at.replace(
                minute=(sold_at.minute // 20) * 20,
                second=0,
                microsecond=0,
            )
        else:
            bucket = sold_at.replace(minute=0, second=0, microsecond=0)
        additional = additional if isinstance(additional, dict) else {}
        additional_key = _additional_key(additional)
        key = (item_id, bucket, quality, additional_key)
        values = grouped[key]
        values.setdefault("additional", additional)
        current_min = values["min_price"]
        current_max = values["max_price"]
        values["min_price"] = price if current_min is None else min(current_min, price)
        values["max_price"] = price if current_max is None else max(current_max, price)
        values["price_sum"] += price
        values["weighted_price_sum"] += price * amount
        values["amount_sum"] += amount
        values["sale_count"] += 1

    return [
        {
            "item_id": item_id,
            "resolution": resolution,
            "bucket_start": bucket,
            "quality": quality,
            "quality_key": UNKNOWN_QUALITY_KEY if quality is None else quality,
            "additional_key": additional_key,
            **values,
        }
        for (item_id, bucket, quality, additional_key), values in grouped.items()
    ]


async def _upsert_aggregate_values(
    session: AsyncSession,
    values: list[dict[str, Any]],
    *,
    additive: bool,
) -> None:
    for start in range(0, len(values), 500):
        aggregate_insert = insert(SaleAggregate).values(values[start : start + 500])
        if additive:
            update_values = {
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
            }
        else:
            update_values = {
                "quality": aggregate_insert.excluded.quality,
                "additional": aggregate_insert.excluded.additional,
                "min_price": aggregate_insert.excluded.min_price,
                "max_price": aggregate_insert.excluded.max_price,
                "price_sum": aggregate_insert.excluded.price_sum,
                "weighted_price_sum": aggregate_insert.excluded.weighted_price_sum,
                "amount_sum": aggregate_insert.excluded.amount_sum,
                "sale_count": aggregate_insert.excluded.sale_count,
                "updated_at": func.now(),
            }
        await session.execute(
            aggregate_insert.on_conflict_do_update(
                constraint="uq_sale_aggregate_bucket",
                set_=update_values,
            )
        )


async def compact_hourly_aggregates_to_daily(
    session: AsyncSession,
    before: datetime,
) -> int:
    bucket_start = func.date_trunc("day", SaleAggregate.bucket_start)
    grouped_result = await session.execute(
        select(
            SaleAggregate.item_id.label("item_id"),
            bucket_start.label("bucket_start"),
            SaleAggregate.quality.label("quality"),
            SaleAggregate.quality_key.label("quality_key"),
            SaleAggregate.additional.label("additional"),
            SaleAggregate.additional_key.label("additional_key"),
            func.min(SaleAggregate.min_price).label("min_price"),
            func.max(SaleAggregate.max_price).label("max_price"),
            func.sum(SaleAggregate.price_sum).label("price_sum"),
            func.sum(SaleAggregate.weighted_price_sum).label("weighted_price_sum"),
            func.sum(SaleAggregate.amount_sum).label("amount_sum"),
            func.sum(SaleAggregate.sale_count).label("sale_count"),
        )
        .where(
            SaleAggregate.resolution == "hour",
            SaleAggregate.bucket_start < before,
        )
        .group_by(
            SaleAggregate.item_id,
            bucket_start,
            SaleAggregate.quality,
            SaleAggregate.quality_key,
            SaleAggregate.additional,
            SaleAggregate.additional_key,
        )
    )
    values = [
        {
            "item_id": str(row["item_id"]),
            "resolution": "day",
            "bucket_start": row["bucket_start"],
            "quality": row["quality"],
            "quality_key": row["quality_key"],
            "additional": row["additional"],
            "additional_key": row["additional_key"],
            "min_price": row["min_price"],
            "max_price": row["max_price"],
            "price_sum": row["price_sum"],
            "weighted_price_sum": row["weighted_price_sum"],
            "amount_sum": row["amount_sum"],
            "sale_count": row["sale_count"],
        }
        for row in grouped_result.mappings()
    ]
    await _upsert_aggregate_values(session, values, additive=True)
    delete_result = await session.execute(
        delete(SaleAggregate).where(
            SaleAggregate.resolution == "hour",
            SaleAggregate.bucket_start < before,
        )
    )
    return int(getattr(delete_result, "rowcount", 0) or 0)


async def delete_aggregates_before(
    session: AsyncSession,
    resolution: str,
    before: datetime,
) -> int:
    result = await session.execute(
        delete(SaleAggregate).where(
            SaleAggregate.resolution == resolution,
            SaleAggregate.bucket_start < before,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def prune_aggregates(
    session: AsyncSession,
    max_points: int,
    item_id: str | None = None,
) -> int:
    if max_points <= 0:
        raise ValueError("max_points must be positive")
    resolution_order = case(
        (SaleAggregate.resolution == "20min", 0),
        (SaleAggregate.resolution == "hour", 1),
        (SaleAggregate.resolution == "day", 2),
        else_=3,
    )
    ranked_query = select(
        SaleAggregate.id.label("id"),
        func.row_number()
        .over(
            partition_by=SaleAggregate.item_id,
            order_by=(
                SaleAggregate.bucket_start.desc(),
                resolution_order,
                SaleAggregate.id.desc(),
            ),
        )
        .label("position"),
    )
    if item_id is not None:
        ranked_query = ranked_query.where(SaleAggregate.item_id == item_id)
    ranked = ranked_query.subquery()
    stale_ids = select(ranked.c.id).where(ranked.c.position > max_points)
    result = await session.execute(delete(SaleAggregate).where(SaleAggregate.id.in_(stale_ids)))
    return int(getattr(result, "rowcount", 0) or 0)


async def prune_hourly_aggregates(
    session: AsyncSession,
    max_points: int,
    item_id: str | None = None,
) -> int:
    return await prune_aggregates(session, max_points, item_id)


async def compact_raw_overflow_to_20min(
    session: AsyncSession,
    max_points: int,
    item_id: str | None = None,
) -> int:
    if max_points <= 0:
        raise ValueError("max_points must be positive")
    ranked_query = select(
        AuctionSale.id.label("id"),
        func.row_number()
        .over(
            partition_by=AuctionSale.item_id,
            order_by=(AuctionSale.sold_at.desc(), AuctionSale.id.desc()),
        )
        .label("position"),
    )
    if item_id is not None:
        ranked_query = ranked_query.where(AuctionSale.item_id == item_id)
    ranked = ranked_query.subquery()
    stale_ids = select(ranked.c.id).where(ranked.c.position > max_points).subquery()
    rows_result = await session.execute(
        select(
            AuctionSale.item_id,
            AuctionSale.sold_at,
            AuctionSale.amount,
            AuctionSale.price,
            AuctionSale.quality,
            AuctionSale.additional,
        ).where(AuctionSale.id.in_(select(stale_ids.c.id)))
    )
    rows = list(rows_result.mappings())
    if not rows:
        return 0
    values = _group_aggregate_values(
        (
            (
                str(row["item_id"]),
                row["sold_at"],
                int(row["amount"]),
                Decimal(row["price"]),
                row["quality"],
                row["additional"],
            )
            for row in rows
        ),
        resolution="20min",
    )
    await _upsert_aggregate_values(session, values, additive=True)
    result = await session.execute(
        delete(AuctionSale).where(AuctionSale.id.in_(select(stale_ids.c.id)))
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _additional_key(additional: dict[str, Any]) -> str:
    canonical = json.dumps(additional, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def prune_raw_sales(
    session: AsyncSession,
    max_points: int,
    item_id: str | None = None,
) -> int:
    if max_points <= 0:
        raise ValueError("max_points must be positive")
    ranked_query = select(
        AuctionSale.id.label("id"),
        func.row_number()
        .over(
            partition_by=AuctionSale.item_id,
            order_by=(AuctionSale.sold_at.desc(), AuctionSale.id.desc()),
        )
        .label("position"),
    )
    if item_id is not None:
        ranked_query = ranked_query.where(AuctionSale.item_id == item_id)
    ranked = ranked_query.subquery()
    stale_ids = select(ranked.c.id).where(ranked.c.position > max_points)
    result = await session.execute(delete(AuctionSale).where(AuctionSale.id.in_(stale_ids)))
    return int(getattr(result, "rowcount", 0) or 0)


async def compact_history(
    session: AsyncSession,
    now: datetime | None = None,
    raw_retention_hours: int = 48,
    hourly_retention_hours: int = 24 * 60,
    max_raw_points_per_item: int = 10_000,
    max_aggregate_points_per_item: int = 15_000,
) -> tuple[int, int, int]:
    current = now or datetime.now(UTC)
    raw_boundary = (current - timedelta(hours=raw_retention_hours)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    hourly_boundary = (current - timedelta(hours=hourly_retention_hours)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    raw_result = await session.execute(
        delete(AuctionSale).where(AuctionSale.sold_at < raw_boundary)
    )
    overflow_count = await compact_raw_overflow_to_20min(
        session,
        max_raw_points_per_item,
    )
    twenty_minute_count = await delete_aggregates_before(session, "20min", raw_boundary)
    daily_count = await compact_hourly_aggregates_to_daily(session, hourly_boundary)
    lots_result = await session.execute(
        delete(AuctionLot).where(
            AuctionLot.active.is_(False),
            AuctionLot.disappeared_at < current - timedelta(hours=48),
        )
    )
    raw_count = int(getattr(raw_result, "rowcount", 0) or 0)
    lots_count = int(getattr(lots_result, "rowcount", 0) or 0)
    raw_count += overflow_count
    aggregate_count = twenty_minute_count + daily_count + await prune_aggregates(
        session,
        max_aggregate_points_per_item,
    )
    return raw_count, lots_count, aggregate_count


async def get_active_lots(
    session: AsyncSession,
    item_id: str,
    observed_at: datetime | None,
) -> Sequence[AuctionLot]:
    if observed_at is None:
        return []
    query = select(AuctionLot).where(
        AuctionLot.item_id == item_id,
        AuctionLot.last_seen_at == observed_at,
    )
    result = await session.scalars(query.order_by(AuctionLot.buyout_price, AuctionLot.end_time))
    return result.all()


async def get_raw_sales(
    session: AsyncSession,
    item_id: str,
    start: datetime,
    end: datetime,
    quality: int | None,
    definition_ids: Sequence[str] = (),
) -> Sequence[AuctionSale]:
    query = select(AuctionSale).where(
        AuctionSale.item_id == item_id,
        AuctionSale.sold_at >= start,
        AuctionSale.sold_at < end,
    )
    if quality is not None:
        query = query.where(AuctionSale.quality == quality)
    query = _apply_definition_filters(query, AuctionSale.additional, definition_ids)
    result = await session.scalars(query.order_by(AuctionSale.sold_at))
    return result.all()


async def get_aggregates(
    session: AsyncSession,
    item_id: str,
    resolution: str,
    start: datetime,
    end: datetime,
    quality: int | None,
    definition_ids: Sequence[str] = (),
    additional_key: str | None = None,
) -> Sequence[SaleAggregate]:
    query = select(SaleAggregate).where(
        SaleAggregate.item_id == item_id,
        SaleAggregate.resolution == resolution,
        SaleAggregate.bucket_start >= start,
        SaleAggregate.bucket_start < end,
    )
    if quality is not None:
        query = query.where(SaleAggregate.quality == quality)
    if additional_key:
        query = query.where(SaleAggregate.additional_key == additional_key)
    query = _apply_definition_filters(query, SaleAggregate.additional, definition_ids)
    result = await session.scalars(query.order_by(SaleAggregate.bucket_start))
    return result.all()


def _apply_definition_filters(
    query: Any,
    additional_column: Any,
    definition_ids: Sequence[str],
) -> Any:
    for definition_id in _normalize_definition_ids(definition_ids):
        query = query.where(_definition_id_condition(additional_column, definition_id))
    return query


def _definition_id_condition(additional_column: Any, definition_id: str) -> Any:
    variants = _definition_id_variants(definition_id)
    return or_(
        *[
            additional_column.contains({"attributes": [{"definitionId": variant}]})
            for variant in variants
        ]
    )


def _normalize_definition_ids(definition_ids: Sequence[str]) -> tuple[str, ...]:
    result = []
    seen = set()
    for value in definition_ids:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _definition_id_variants(definition_id: str) -> tuple[str, ...]:
    normalized = definition_id.strip()
    base = normalized
    for suffix in ("_pre", "_suf", "_aff"):
        if normalized.endswith(suffix):
            base = normalized[: -len(suffix)]
            break
    variants = [normalized, base, f"{base}_pre", f"{base}_suf", f"{base}_aff"]
    return tuple(dict.fromkeys(variant for variant in variants if variant))
