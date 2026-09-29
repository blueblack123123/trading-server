import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.history.repository import _additional_key
from app.modules.market.service import read_cheap_lots


class _Result:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self.rows


def test_read_cheap_lots_uses_longer_period_when_daily_sales_are_sparse() -> None:
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    session = AsyncMock()
    session.execute.side_effect = [
        _Result(
            [
                _lot("lot-1", "item-1", "Дешёвый предмет", Decimal("700"), 1, now),
            ]
        ),
        _Result([]),
        _Result(
            [
                SimpleNamespace(
                    item_id="item-1",
                    fair_price=Decimal("1000"),
                    sale_count=8,
                    amount_sold=12,
                )
            ]
        ),
    ]

    result = asyncio.run(
        read_cheap_lots(
            session,
            min_discount_percent=Decimal("20"),
            min_sales_count=3,
            now=now,
        )
    )

    assert result.total == 1
    lot = result.lots[0]
    assert lot.item_id == "item-1"
    assert lot.unit_price == Decimal("700")
    assert lot.fair_unit_price == Decimal("1000")
    assert lot.discount_percent == Decimal("30.0")
    assert lot.expected_profit == Decimal("300")
    assert lot.period_hours == 24 * 7
    assert lot.match_level == "exact"
    assert result.active_lots_count == 1
    assert result.history_lots_count == 1
    assert result.exact_history_lots_count == 1
    assert result.fallback_history_lots_count == 0


def test_read_cheap_lots_prefers_exact_variant_over_broad_item_price() -> None:
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    session = AsyncMock()
    empty_key = _additional_key({})
    session.execute.side_effect = [
        _Result(
            [
                _lot(
                    "lot-1",
                    "item-1",
                    "Вариант предмета",
                    Decimal("1000"),
                    1,
                    now,
                    quality=3,
                ),
            ]
        ),
        _Result(
            [
                SimpleNamespace(
                    item_id="item-1",
                    quality_key=3,
                    additional_key=empty_key,
                    weighted_price_sum=Decimal("1200"),
                    sale_count=3,
                    amount_sold=1,
                ),
                SimpleNamespace(
                    item_id="item-1",
                    quality_key=4,
                    additional_key=empty_key,
                    weighted_price_sum=Decimal("20000"),
                    sale_count=10,
                    amount_sold=10,
                ),
            ]
        ),
    ]

    result = asyncio.run(
        read_cheap_lots(
            session,
            min_discount_percent=Decimal("20"),
            min_sales_count=3,
            now=now,
        )
    )

    assert result.total == 0
    assert result.history_lots_count == 1
    assert result.exact_history_lots_count == 1
    assert result.fallback_history_lots_count == 0


def test_read_cheap_lots_filters_small_discounts() -> None:
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    session = AsyncMock()
    session.execute.side_effect = [
        _Result(
            [
                _lot("lot-1", "item-1", "Почти рынок", Decimal("950"), 1, now),
            ]
        ),
        _Result(
            [
                SimpleNamespace(
                    item_id="item-1",
                    fair_price=Decimal("1000"),
                    sale_count=10,
                    amount_sold=10,
                )
            ]
        ),
    ]

    result = asyncio.run(
        read_cheap_lots(
            session,
            min_discount_percent=Decimal("20"),
            min_sales_count=3,
            now=now,
        )
    )

    assert result.total == 0
    assert result.lots == []


def _lot(
    fingerprint: str,
    item_id: str,
    name: str,
    buyout_price: Decimal,
    amount: int,
    now: datetime,
    quality: int | None = None,
    additional: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        fingerprint=fingerprint,
        item_id=item_id,
        item_name=name,
        amount=amount,
        buyout_price=buyout_price,
        quality=quality,
        additional=additional or {},
        end_time=now + timedelta(hours=4),
        last_seen_at=now,
    )
