# ruff: noqa: E402, I001
import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import async_session_factory
from app.modules.history.models import AuctionSale, SaleAggregate
from app.modules.history.repository import _group_aggregate_values, _upsert_aggregate_values


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild hourly sale aggregates from raw auction sales."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=48,
        help="How many recent hours to rebuild.",
    )
    parser.add_argument(
        "--item-id",
        default=None,
        help="Optional item id to rebuild.",
    )
    return parser.parse_args()


async def _rebuild(hours: int, item_id: str | None) -> tuple[int, int]:
    if hours <= 0:
        raise ValueError("hours must be positive")

    now = datetime.now(UTC)
    since = (now - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)

    async with async_session_factory() as session, session.begin():
        query = select(
            AuctionSale.item_id,
            AuctionSale.sold_at,
            AuctionSale.amount,
            AuctionSale.price,
            AuctionSale.quality,
            AuctionSale.additional,
        ).where(AuctionSale.sold_at >= since)
        delete_query = delete(SaleAggregate).where(
            SaleAggregate.resolution == "hour",
            SaleAggregate.bucket_start >= since,
        )
        if item_id is not None:
            query = query.where(AuctionSale.item_id == item_id)
            delete_query = delete_query.where(SaleAggregate.item_id == item_id)

        rows = list((await session.execute(query)).mappings())
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

        await session.execute(delete_query)
        await _upsert_aggregate_values(session, values, additive=False)

    return len(rows), len(values)


async def _main() -> None:
    args = _parse_args()
    rows, aggregates = await _rebuild(args.hours, args.item_id)
    print(f"Rebuilt {aggregates} hourly aggregates from {rows} raw sales.")


if __name__ == "__main__":
    asyncio.run(_main())
