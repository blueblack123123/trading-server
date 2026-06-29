import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.main import app
from app.modules.admin.schemas import MarketStatus
from app.modules.history.models import HistoryPollState, MarketItem
from app.modules.items.service import read_item_status


def test_read_item_status_returns_configured_and_effective_statuses() -> None:
    session = AsyncMock()
    item = MarketItem(
        id="item-1",
        name="Item",
        configured_status=int(MarketStatus.AUTO),
        effective_status=int(MarketStatus.HOT),
    )
    state = HistoryPollState(
        item_id=item.id,
        next_poll_at=datetime(2026, 6, 29, 12, 1, tzinfo=UTC),
        last_polled_at=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
        last_success_at=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
        consecutive_errors=0,
    )
    session.get.side_effect = [item, state]

    response = asyncio.run(read_item_status(session, item.id))

    assert response.configured_status == "AUTO"
    assert response.effective_status == "HOT"
    assert response.last_success_at == state.last_success_at
    assert response.next_poll_at == state.next_poll_at


def test_read_item_status_rejects_unknown_item() -> None:
    session = AsyncMock()
    session.get.return_value = None

    with pytest.raises(LookupError, match="item is not configured"):
        asyncio.run(read_item_status(session, "missing"))


def test_item_status_endpoint_is_exposed() -> None:
    operation = app.openapi()["paths"]["/api/v1/items/{item_id}/status"]["get"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ItemStatusResponse"
    }
