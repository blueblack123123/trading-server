import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

from sqlalchemy.engine import RowMapping

from app.modules.history.domain import SaleRecord
from app.modules.history.repository import (
    _definition_id_variants,
    _increment_aggregates,
    compact_history,
    replace_daily_aggregates,
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
            "additional": {"attributes": [{"definitionId": "one"}]},
        },
        {
            "item_id": "item-1",
            "sold_at": datetime(2026, 6, 29, 10, 45, tzinfo=UTC),
            "amount": 1,
            "price": Decimal("120"),
            "quality": 3,
            "additional": {"attributes": [{"definitionId": "two"}]},
        },
    ]

    asyncio.run(_increment_aggregates(session, cast(list[RowMapping], rows)))

    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert "hour" in statement.compile().params.values()
    assert "additional_key" in str(statement)


def test_definition_id_variants_include_module_suffixes_and_base_id() -> None:
    assert _definition_id_variants("concentration") == (
        "concentration",
        "concentration_pre",
        "concentration_suf",
        "concentration_aff",
    )
    assert _definition_id_variants("hip_spread_suf") == (
        "hip_spread_suf",
        "hip_spread",
        "hip_spread_pre",
        "hip_spread_aff",
    )


def test_replace_hourly_aggregates_overwrites_instead_of_incrementing() -> None:
    session = AsyncMock()
    records = [
        SaleRecord(
            item_id="item-1",
            sold_at=datetime(2026, 6, 20, 10, 15, tzinfo=UTC),
            amount=2,
            price=Decimal("100"),
            quality=3,
            additional={"attributes": [{"definitionId": "one"}]},
            fingerprint="one",
        ),
        SaleRecord(
            item_id="item-1",
            sold_at=datetime(2026, 6, 20, 10, 45, tzinfo=UTC),
            amount=1,
            price=Decimal("120"),
            quality=3,
            additional={"attributes": [{"definitionId": "one"}]},
            fingerprint="two",
        ),
    ]

    count = asyncio.run(replace_hourly_aggregates(session, records))

    assert count == 1
    statement = str(session.execute.await_args.args[0])
    assert "sale_aggregates.sale_count +" not in statement
    assert "excluded.sale_count" in statement


def test_replace_daily_aggregates_groups_by_day() -> None:
    session = AsyncMock()
    records = [
        SaleRecord(
            item_id="item-1",
            sold_at=datetime(2026, 6, 20, 10, 15, tzinfo=UTC),
            amount=2,
            price=Decimal("100"),
            quality=3,
            additional={"attributes": [{"definitionId": "one"}]},
            fingerprint="one",
        ),
        SaleRecord(
            item_id="item-1",
            sold_at=datetime(2026, 6, 20, 23, 45, tzinfo=UTC),
            amount=1,
            price=Decimal("120"),
            quality=3,
            additional={"attributes": [{"definitionId": "two"}]},
            fingerprint="two",
        ),
    ]

    count = asyncio.run(replace_daily_aggregates(session, records))

    assert count == 2
    assert "day" in session.execute.await_args.args[0].compile().params.values()


def test_compact_history_prunes_old_aggregates() -> None:
    session = AsyncMock()
    session.execute.side_effect = [
        SimpleNamespace(rowcount=4),
        SimpleNamespace(rowcount=2),
    ]

    with (
        patch(
            "app.modules.history.repository.compact_hourly_aggregates_to_daily",
            AsyncMock(return_value=7),
        ),
        patch(
            "app.modules.history.repository.compact_raw_overflow_to_20min",
            AsyncMock(return_value=5),
        ),
        patch("app.modules.history.repository.delete_aggregates_before", AsyncMock(return_value=6)),
        patch("app.modules.history.repository.prune_aggregates", AsyncMock(return_value=3)),
    ):
        deleted = asyncio.run(compact_history(session, now=datetime(2026, 6, 29, 12, tzinfo=UTC)))

    assert deleted == (9, 2, 16)
    assert session.execute.await_count == 2
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert "auction_sales" in statements[0]
