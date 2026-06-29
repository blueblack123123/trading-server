import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from sqlalchemy.engine import RowMapping

from app.modules.history.domain import SaleRecord
from app.modules.history.repository import (
    _increment_aggregates,
    compact_history,
    replace_hourly_aggregates,
)


def test_increment_aggregates_creates_only_an_hourly_bucket() -> None:
    session = AsyncMock()
    rows = [
        {
            "item_id": "item-1",
            "sold_at": datetime(2026, 6, 29, 10, 15, tzinfo=UTC),
            "amount": 2,
            "price": Decimal("100"),
            "quality": 3,
        },
        {
            "item_id": "item-1",
            "sold_at": datetime(2026, 6, 29, 10, 45, tzinfo=UTC),
            "amount": 1,
            "price": Decimal("120"),
            "quality": 3,
        },
    ]

    asyncio.run(_increment_aggregates(session, cast(list[RowMapping], rows)))

    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert "hour" in statement.compile().params.values()


def test_replace_hourly_aggregates_overwrites_instead_of_incrementing() -> None:
    session = AsyncMock()
    records = [
        SaleRecord(
            item_id="item-1",
            sold_at=datetime(2026, 6, 20, 10, 15, tzinfo=UTC),
            amount=2,
            price=Decimal("100"),
            quality=3,
            additional={},
            fingerprint="one",
        ),
        SaleRecord(
            item_id="item-1",
            sold_at=datetime(2026, 6, 20, 10, 45, tzinfo=UTC),
            amount=1,
            price=Decimal("120"),
            quality=3,
            additional={},
            fingerprint="two",
        ),
    ]

    count = asyncio.run(replace_hourly_aggregates(session, records))

    assert count == 1
    statement = str(session.execute.await_args.args[0])
    assert "sale_aggregates.sale_count +" not in statement
    assert "excluded.sale_count" in statement


def test_compact_history_prunes_old_aggregates() -> None:
    session = AsyncMock()
    session.execute.side_effect = [
        SimpleNamespace(rowcount=4),
        SimpleNamespace(rowcount=2),
        SimpleNamespace(rowcount=3),
    ]

    deleted = asyncio.run(compact_history(session, now=datetime(2026, 6, 29, 12, tzinfo=UTC)))

    assert deleted == (4, 2, 3)
    assert session.execute.await_count == 3
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert "sale_aggregates" in statements[-1]
