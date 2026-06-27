from typing import Any

import httpx

from app.core.config import settings


class StalzoneClient:
    def __init__(self) -> None:
        self._base_url = settings.stalzone_base_url
        self._headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0",
            "X-Internal-Key": settings.stalzone_internal_key,
        }

    async def get_auction_history(self, item_id: str, region: str = "ru") -> Any:
        return await self._get(
            "/auction-history",
            params={"region": region, "id": item_id},
        )

    async def get_available_lots(self, item_id: str, region: str = "ru") -> Any:
        return await self._get(
            "/available-lots",
            params={"region": region, "id": item_id},
        )

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=20.0) as client:
            response = await client.get(path, headers=self._headers, params=params)

        response.raise_for_status()
        return response.json()