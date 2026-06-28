import asyncio
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings


class OAuthTokenProvider:
    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self, client: httpx.AsyncClient) -> str:
        if self._token is not None and monotonic() < self._expires_at:
            return self._token

        async with self._lock:
            if self._token is not None and monotonic() < self._expires_at:
                return self._token

            client_secret = settings.stalzone_client_secret.get_secret_value()
            if not settings.stalzone_client_id or not client_secret:
                raise RuntimeError("STALZONE_CLIENT_ID and STALZONE_CLIENT_SECRET are required")

            response = await client.post(
                settings.stalzone_oauth_token_url,
                data={
                    "client_id": settings.stalzone_client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                    "scope": "",
                },
            )
            response.raise_for_status()
            payload = response.json()
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                raise RuntimeError("OAuth response contains no access_token")

            expires_in = payload.get("expires_in", 3600)
            expires_seconds = int(expires_in) if isinstance(expires_in, (int, str)) else 3600
            self._token = token
            self._expires_at = monotonic() + max(60, expires_seconds - 60)
            return token

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0


token_provider = OAuthTokenProvider()


class StalzoneClient:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "StalzoneClient":
        self._client = httpx.AsyncClient(
            base_url=settings.stalzone_base_url,
            headers={"Accept": "application/json"},
            timeout=30.0,
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
        region: str | None = None,
        limit: int = 200,
        offset: int = 0,
        additional: bool = True,
    ) -> Any:
        return await self._get(
            self._auction_path(item_id, "history", region),
            params={
                "limit": str(limit),
                "offset": str(offset),
                "additional": str(additional).lower(),
            },
        )

    async def get_available_lots(
        self,
        item_id: str,
        region: str | None = None,
        limit: int = 200,
        offset: int = 0,
        additional: bool = True,
    ) -> Any:
        return await self._get(
            self._auction_path(item_id, "lots", region),
            params={
                "limit": str(limit),
                "offset": str(offset),
                "additional": str(additional).lower(),
                "sort": "buyout_price",
                "order": "asc",
            },
        )

    @staticmethod
    def _auction_path(item_id: str, endpoint: str, region: str | None) -> str:
        selected_region = (region or settings.stalzone_region).upper()
        return f"/{quote(selected_region, safe='')}/auction/{quote(item_id, safe='')}/{endpoint}"

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        if self._client is not None:
            return await self._authenticated_get(self._client, path, params)

        async with httpx.AsyncClient(
            base_url=settings.stalzone_base_url,
            headers={"Accept": "application/json"},
            timeout=30.0,
        ) as client:
            return await self._authenticated_get(client, path, params)

    async def _authenticated_get(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, str],
    ) -> Any:
        token = await token_provider.get_token(client)
        response = await client.get(
            path,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            token_provider.invalidate()
            token = await token_provider.get_token(client)
            response = await client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        return response.json()
