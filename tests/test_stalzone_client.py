import asyncio

import httpx
from pydantic import SecretStr

from app.clients.stalzone import StalzoneClient, token_provider
from app.core.config import settings


def test_client_gets_oauth_token_and_calls_official_auction_api(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "exbo.net":
            return httpx.Response(
                200,
                json={"access_token": "test-token", "token_type": "Bearer", "expires_in": 3600},
            )
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"total": 0, "prices": []})

    monkeypatch.setattr(settings, "stalzone_client_id", "882")
    monkeypatch.setattr(settings, "stalzone_client_secret", SecretStr("secret"))
    token_provider.invalidate()

    async def run() -> None:
        client = StalzoneClient()
        client._client = httpx.AsyncClient(
            base_url="https://eapi.stalzone.com",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.get_auction_history("item-1", limit=200, offset=20)
        finally:
            await client._client.aclose()
            client._client = None

    asyncio.run(run())

    assert len(requests) == 2
    assert requests[1].url.path == "/RU/auction/item-1/history"
    assert requests[1].url.params["limit"] == "200"
    assert requests[1].url.params["offset"] == "20"
