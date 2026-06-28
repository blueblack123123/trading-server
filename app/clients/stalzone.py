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
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "StalzoneClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=20.0,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_auction_history(
        self,
        item_id: str,
        region: str = "ru",
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        params = {"region": region, "id": item_id}
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)
        return await self._get(
            "/auction-history",
            params=params,
        )

    async def get_available_lots(
        self,
        item_id: str,
        region: str = "ru",
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        params = {"region": region, "id": item_id}
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)
        return await self._get(
            "/available-lots",
            params=params,
        )

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        if self._client is not None:
            response = await self._client.get(path, params=params)
        else:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=20.0) as client:
                response = await client.get(path, headers=self._headers, params=params)

        response.raise_for_status()
        return response.json()
