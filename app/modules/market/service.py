from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.modules.history.models import AuctionLot, MarketItem, SaleAggregate
from app.modules.history.repository import (
    UNKNOWN_QUALITY_KEY,
    _additional_key,
    _aggregate_additional,
)
from app.modules.market.schemas import CheapLot, CheapLotsResponse

PERIOD_HOURS = (24, 24 * 7, 24 * 30)


@dataclass(frozen=True)
class _LotMarketKey:
    item_id: str
    quality_key: int
    additional_key: str


@dataclass(frozen=True)
class _FairPrice:
    unit_price: Decimal
    sale_count: int
    amount_sold: int
    period_hours: int
    match_level: str


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
            active_lots_count=0,
            history_lots_count=0,
            exact_history_lots_count=0,
            fallback_history_lots_count=0,
            total=0,
            lots=[],
        )

    lot_keys = {lot.fingerprint: _lot_market_key(lot) for lot in lots}
    exact_prices, fallback_prices = await _read_fair_prices(
        session,
        set(lot_keys.values()),
        current,
        min_sales_count,
    )
    rows: list[CheapLot] = []
    history_lots_count = 0
    exact_history_lots_count = 0
    fallback_history_lots_count = 0
    for lot in lots:
        lot_key = lot_keys[lot.fingerprint]
        fair = exact_prices.get(lot_key)
        if fair is None:
            fair = fallback_prices.get(lot.item_id)
        if fair is not None:
            history_lots_count += 1
            if fair.match_level == "exact":
                exact_history_lots_count += 1
            else:
                fallback_history_lots_count += 1
        if fair is None or lot.amount <= 0 or lot.buyout_price <= 0:
            continue
        unit_price = lot.buyout_price / Decimal(lot.amount)
        if fair.unit_price <= 0 or unit_price <= 0:
            continue
        discount_percent = (fair.unit_price - unit_price) / fair.unit_price * Decimal("100")
        if discount_percent < min_discount_percent:
            continue
        expected_profit = (fair.unit_price - unit_price) * Decimal(lot.amount)
        expected_profit_percent = expected_profit / lot.buyout_price * Decimal("100")
        rows.append(
            CheapLot(
                item_id=lot.item_id,
                item_name=lot.item_name,
                fingerprint=lot.fingerprint,
                amount=lot.amount,
                buyout_price=lot.buyout_price,
                unit_price=unit_price,
                fair_unit_price=fair.unit_price,
                discount_percent=discount_percent,
                expected_profit=expected_profit,
                expected_profit_percent=expected_profit_percent,
                sales_count=fair.sale_count,
                amount_sold=fair.amount_sold,
                period_hours=fair.period_hours,
                match_level=fair.match_level,
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
        active_lots_count=len(lots),
        history_lots_count=history_lots_count,
        exact_history_lots_count=exact_history_lots_count,
        fallback_history_lots_count=fallback_history_lots_count,
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
    lot_keys: set[_LotMarketKey],
    now: datetime,
    min_sales_count: int,
) -> tuple[dict[_LotMarketKey, _FairPrice], dict[str, _FairPrice]]:
    """Подбирает справедливую цену по первому периоду с достаточным числом продаж."""
    remaining_exact = set(lot_keys)
    remaining_items = {key.item_id for key in lot_keys}
    exact_result: dict[_LotMarketKey, _FairPrice] = {}
    fallback_result: dict[str, _FairPrice] = {}
    for period_hours in PERIOD_HOURS:
        if not remaining_exact and not remaining_items:
            break
        start = now - timedelta(hours=period_hours)
        rows = (
            await session.execute(
                select(
                    SaleAggregate.item_id,
                    SaleAggregate.quality_key,
                    SaleAggregate.additional_key,
                    func.sum(SaleAggregate.weighted_price_sum).label("weighted_price_sum"),
                    func.sum(SaleAggregate.amount_sum).label("amount_sold"),
                    func.sum(SaleAggregate.sale_count).label("sale_count"),
                )
                .where(
                    SaleAggregate.item_id.in_(remaining_items),
                    SaleAggregate.resolution == "hour",
                    SaleAggregate.bucket_start >= start,
                    SaleAggregate.bucket_start < now,
                    SaleAggregate.amount_sum > 0,
                )
                .group_by(
                    SaleAggregate.item_id,
                    SaleAggregate.quality_key,
                    SaleAggregate.additional_key,
                )
            )
        ).all()
        fallback_groups: dict[str, dict[str, Decimal | int]] = {}
        for row in rows:
            sale_count = int(row.sale_count or 0)
            amount_sold = int(row.amount_sold or 0)
            raw_weighted_price_sum = getattr(row, "weighted_price_sum", None)
            if raw_weighted_price_sum is None and getattr(row, "fair_price", None) is not None:
                raw_weighted_price_sum = Decimal(row.fair_price) * Decimal(amount_sold)
            weighted_price_sum = Decimal(raw_weighted_price_sum or 0)
            if amount_sold <= 0:
                continue
            item_id = str(row.item_id)
            fallback = fallback_groups.setdefault(
                item_id,
                {
                    "weighted_price_sum": Decimal("0"),
                    "amount_sold": 0,
                    "sale_count": 0,
                },
            )
            fallback["weighted_price_sum"] += weighted_price_sum
            fallback["amount_sold"] += amount_sold
            fallback["sale_count"] += sale_count

            key = _LotMarketKey(
                item_id=item_id,
                quality_key=int(getattr(row, "quality_key", UNKNOWN_QUALITY_KEY)),
                additional_key=str(getattr(row, "additional_key", _additional_key({}))),
            )
            if key not in remaining_exact or sale_count < min_sales_count:
                continue
            exact_result[key] = _FairPrice(
                unit_price=weighted_price_sum / Decimal(amount_sold),
                sale_count=sale_count,
                amount_sold=amount_sold,
                period_hours=period_hours,
                match_level="exact",
            )
            remaining_exact.discard(key)

        for item_id, values in fallback_groups.items():
            if item_id not in remaining_items:
                continue
            sale_count = int(values["sale_count"])
            amount_sold = int(values["amount_sold"])
            if sale_count < min_sales_count or amount_sold <= 0:
                continue
            fallback_result[item_id] = _FairPrice(
                unit_price=Decimal(values["weighted_price_sum"]) / Decimal(amount_sold),
                sale_count=sale_count,
                amount_sold=amount_sold,
                period_hours=period_hours,
                match_level="item",
            )
            remaining_items.discard(item_id)
    return exact_result, fallback_result


def _lot_market_key(lot) -> _LotMarketKey:
    """Возвращает ключ варианта лота, совместимый с агрегатами истории продаж."""
    aggregate_additional = _aggregate_additional(
        str(lot.item_id),
        lot.additional if isinstance(lot.additional, dict) else {},
        lot.quality,
    )
    return _LotMarketKey(
        item_id=str(lot.item_id),
        quality_key=UNKNOWN_QUALITY_KEY if lot.quality is None else int(lot.quality),
        additional_key=_additional_key(aggregate_additional),
    )
