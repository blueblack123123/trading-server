from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.modules.history.models import AuctionLot, MarketItem, SaleAggregate
from app.modules.market.schemas import CheapLot, CheapLotsResponse

PERIOD_HOURS = (24, 24 * 7, 24 * 30)


async def read_cheap_lots(
    session: AsyncSession,
    *,
    min_discount_percent: Decimal = Decimal("15"),
    min_sales_count: int = 3,
    limit: int = 100,
    now: datetime | None = None,
) -> CheapLotsResponse:
    """Находит активные лоты, которые заметно дешевле исторической цены продажи."""
    current = now or datetime.now(UTC)
    lots = await _read_active_lots(session, current)
    if not lots:
        return CheapLotsResponse(
            generated_at=current,
            min_discount_percent=min_discount_percent,
            min_sales_count=min_sales_count,
            total=0,
            lots=[],
        )

    item_ids = {lot.item_id for lot in lots}
    fair_prices = await _read_fair_prices(session, item_ids, current, min_sales_count)
    rows: list[CheapLot] = []
    for lot in lots:
        fair = fair_prices.get(lot.item_id)
        if fair is None or lot.amount <= 0 or lot.buyout_price <= 0:
            continue
        fair_price, sale_count, amount_sold, period_hours = fair
        unit_price = lot.buyout_price / Decimal(lot.amount)
        if fair_price <= 0 or unit_price <= 0:
            continue
        discount_percent = (fair_price - unit_price) / fair_price * Decimal("100")
        if discount_percent < min_discount_percent:
            continue
        expected_profit = (fair_price - unit_price) * Decimal(lot.amount)
        expected_profit_percent = expected_profit / lot.buyout_price * Decimal("100")
        rows.append(
            CheapLot(
                item_id=lot.item_id,
                item_name=lot.item_name,
                fingerprint=lot.fingerprint,
                amount=lot.amount,
                buyout_price=lot.buyout_price,
                unit_price=unit_price,
                fair_unit_price=fair_price,
                discount_percent=discount_percent,
                expected_profit=expected_profit,
                expected_profit_percent=expected_profit_percent,
                sales_count=sale_count,
                amount_sold=amount_sold,
                period_hours=period_hours,
                quality=lot.quality,
                additional=lot.additional,
                end_time=lot.end_time,
                last_seen_at=lot.last_seen_at,
            )
        )

    rows.sort(
        key=lambda row: (
            row.expected_profit,
            row.discount_percent,
            row.sales_count,
        ),
        reverse=True,
    )
    limited = rows[:limit]
    return CheapLotsResponse(
        generated_at=current,
        min_discount_percent=min_discount_percent,
        min_sales_count=min_sales_count,
        total=len(rows),
        lots=limited,
    )


async def _read_active_lots(session: AsyncSession, now: datetime) -> list:
    """Возвращает актуальные активные лоты из локальной базы аукциона."""
    statement = (
        select(
            AuctionLot.fingerprint,
            AuctionLot.item_id,
            MarketItem.name.label("item_name"),
            AuctionLot.amount,
            AuctionLot.buyout_price,
            AuctionLot.quality,
            AuctionLot.additional,
            AuctionLot.end_time,
            AuctionLot.last_seen_at,
        )
        .join(MarketItem, MarketItem.id == AuctionLot.item_id)
        .where(
            AuctionLot.active.is_(True),
            AuctionLot.buyout_price > 0,
            AuctionLot.amount > 0,
            AuctionLot.end_time > now,
        )
    )
    return list((await session.execute(statement)).all())


async def _read_fair_prices(
    session: AsyncSession,
    item_ids: set[str],
    now: datetime,
    min_sales_count: int,
) -> dict[str, tuple[Decimal, int, int, int]]:
    """Подбирает справедливую цену по первому периоду с достаточным числом продаж."""
    remaining = set(item_ids)
    result: dict[str, tuple[Decimal, int, int, int]] = {}
    for period_hours in PERIOD_HOURS:
        if not remaining:
            break
        start = now - timedelta(hours=period_hours)
        rows = (
            await session.execute(
                select(
                    SaleAggregate.item_id,
                    (
                        func.sum(SaleAggregate.weighted_price_sum)
                        / func.sum(SaleAggregate.amount_sum)
                    ).label("fair_price"),
                    func.sum(SaleAggregate.sale_count).label("sale_count"),
                    func.sum(SaleAggregate.amount_sum).label("amount_sold"),
                )
                .where(
                    SaleAggregate.item_id.in_(remaining),
                    SaleAggregate.resolution == "hour",
                    SaleAggregate.bucket_start >= start,
                    SaleAggregate.bucket_start < now,
                    SaleAggregate.amount_sum > 0,
                )
                .group_by(SaleAggregate.item_id)
            )
        ).all()
        for row in rows:
            sale_count = int(row.sale_count or 0)
            if sale_count < min_sales_count or row.fair_price is None:
                continue
            result[str(row.item_id)] = (
                Decimal(row.fair_price),
                sale_count,
                int(row.amount_sold or 0),
                period_hours,
            )
            remaining.discard(str(row.item_id))
    return result
