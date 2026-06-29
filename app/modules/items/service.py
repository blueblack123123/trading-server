from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.schemas import MarketStatus
from app.modules.history.models import HistoryPollState, MarketItem
from app.modules.items.schemas import ItemStatusResponse, MarketStatusName


async def read_item_statuses(session: AsyncSession) -> list[ItemStatusResponse]:
    statement = (
        select(MarketItem, HistoryPollState)
        .outerjoin(HistoryPollState, HistoryPollState.item_id == MarketItem.id)
        .order_by(MarketItem.name, MarketItem.id)
    )
    rows = (await session.execute(statement)).all()
    return [_build_status_response(item, state) for item, state in rows]


def _build_status_response(
    item: MarketItem,
    state: HistoryPollState | None,
) -> ItemStatusResponse:
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
