import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from app.main import app
from app.modules.history import service


def _lot(index: int) -> dict[str, Any]:
    return {
        "amount": 1,
        "startPrice": index,
        "currentPrice": index + 1,
        "buyoutPrice": index + 2,
        "startTime": f"2026-06-28T10:{index % 60:02d}:00Z",
        "endTime": f"2026-06-29T10:{index % 60:02d}:00Z",
        "additional": {"qlt": index % 6, "index": index},
    }


def _client_for_pages(pages: list[dict[str, Any]]) -> type:
    class FakeClient:
        def __init__(self) -> None:
            self._pages: Iterator[dict[str, Any]] = iter(pages)

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get_available_lots(self, **kwargs: object) -> dict[str, Any]:
            return next(self._pages)

    return FakeClient


def test_fetch_all_lots_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    lots = [_lot(index) for index in range(201)]
    pages = [
        {"total": 201, "lots": lots[:200]},
        {"total": 201, "lots": lots[200:]},
    ]
    monkeypatch.setattr(service, "StalzoneClient", _client_for_pages(pages))

    records, total, complete = asyncio.run(service._fetch_all_lots("item-1"))

    assert total == 201
    assert len(records) == 201
    assert complete is True
    assert records[-1].additional == {"qlt": 2, "index": 200}


def test_fetch_all_lots_rejects_partial_response(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"total": 201, "lots": [_lot(index) for index in range(200)]},
        {"total": 201, "lots": []},
    ]
    monkeypatch.setattr(service, "StalzoneClient", _client_for_pages(pages))

    with pytest.raises(RuntimeError, match="incomplete"):
        asyncio.run(service._fetch_all_lots("item-1"))


def test_lots_endpoint_has_no_server_side_filters() -> None:
    operation = app.openapi()["paths"]["/api/v1/items/{item_id}/lots"]["get"]

    assert [parameter["name"] for parameter in operation["parameters"]] == ["item_id"]
