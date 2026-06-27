from typing import Any

from fastapi import APIRouter, Query

from app.clients.stalzone import StalzoneClient

router = APIRouter()


@router.get("/{item_id}/history")
async def get_item_history(
    item_id: str,
    region: str = Query(default="ru"),
) -> Any:
    client = StalzoneClient()
    return await client.get_auction_history(item_id=item_id, region=region)


@router.get("/{item_id}/lots")
async def get_item_lots(
    item_id: str,
    region: str = Query(default="ru"),
) -> Any:
    client = StalzoneClient()
    return await client.get_available_lots(item_id=item_id, region=region)