import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from app.main import app
from app.modules.admin.schemas import MarketStatus
from app.modules.history.models import HistoryPollState, MarketItem
from app.modules.items.service import read_item_statuses


def test_read_item_statuses_returns_configured_and_effective_statuses() -> None:
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
    result = MagicMock()
    result.all.return_value = [(item, state)]
    session.execute.return_value = result

    responses = asyncio.run(read_item_statuses(session))

    assert len(responses) == 1
    response = responses[0]
    assert response.configured_status == "AUTO"
    assert response.effective_status == "HOT"
    assert response.last_success_at == state.last_success_at
    assert response.next_poll_at == state.next_poll_at


def test_read_item_statuses_returns_an_empty_list() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    session.execute.return_value = result

    assert asyncio.run(read_item_statuses(session)) == []


def test_item_statuses_endpoint_is_exposed() -> None:
    operation = app.openapi()["paths"]["/api/v1/items/statuses"]["get"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "items": {"$ref": "#/components/schemas/ItemStatusResponse"},
        "title": "Response Get Item Statuses Api V1 Items Statuses Get",
        "type": "array",
    }
