import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from sqlalchemy.engine import RowMapping

from app.modules.history.repository import _increment_aggregates, compact_history


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
    assert statement.compile().params["resolution"] == "hour"


def test_compact_history_does_not_delete_aggregates() -> None:
    session = AsyncMock()
    session.execute.side_effect = [
        SimpleNamespace(rowcount=4),
        SimpleNamespace(rowcount=2),
    ]

    deleted = asyncio.run(compact_history(session, now=datetime(2026, 6, 29, 12, tzinfo=UTC)))

    assert deleted == (4, 2)
    assert session.execute.await_count == 2
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert all("sale_aggregates" not in statement for statement in statements)
