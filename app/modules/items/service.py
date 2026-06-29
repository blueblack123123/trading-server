from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.schemas import MarketStatus
from app.modules.history.models import HistoryPollState, MarketItem
from app.modules.items.schemas import ItemStatusResponse, MarketStatusName


async def read_item_status(session: AsyncSession, item_id: str) -> ItemStatusResponse:
    item = await session.get(MarketItem, item_id)
    if item is None:
        raise LookupError("item is not configured")

    state = await session.get(HistoryPollState, item_id)
    return ItemStatusResponse(
        item_id=item.id,
        name=item.name,
        configured_status=_status_name(item.configured_status),
        effective_status=_status_name(item.effective_status),
        last_polled_at=state.last_polled_at if state is not None else None,
        last_success_at=state.last_success_at if state is not None else None,
        next_poll_at=state.next_poll_at if state is not None else None,
        consecutive_errors=state.consecutive_errors if state is not None else 0,
    )


def _status_name(value: int) -> MarketStatusName:
    return MarketStatus(value).name  # type: ignore[return-value]
