import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.modules.history import service


def test_auto_resolution_uses_hourly_aggregates_for_old_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_aggregates = AsyncMock(return_value=[])
    get_raw_sales = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "get_aggregates", get_aggregates)
    monkeypatch.setattr(service, "get_raw_sales", get_raw_sales)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 2, 1, tzinfo=UTC)

    asyncio.run(
        service.read_history(
            session=AsyncMock(),
            item_id="item-1",
            start=start,
            end=end,
            quality=None,
            resolution="auto",
        )
    )

    get_aggregates.assert_awaited_once()
    aggregate_call = get_aggregates.await_args
    assert aggregate_call is not None
    assert aggregate_call.args[2:5] == ("hour", start, end)
    get_raw_sales.assert_not_awaited()
