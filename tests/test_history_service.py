import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.history import service


def test_auto_resolution_uses_hourly_aggregates_for_old_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_aggregates = AsyncMock(return_value=[])
    get_raw_sales = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "get_aggregates", get_aggregates)
    monkeypatch.setattr(service, "get_raw_sales", get_raw_sales)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2020, 2, 1, tzinfo=UTC)

    asyncio.run(
        service.read_history(
            session=AsyncMock(),
            item_id="item-1",
            start=start,
            end=end,
            quality=None,
            resolution="auto",
        )
    )

    get_aggregates.assert_awaited_once()
    aggregate_call = get_aggregates.await_args
    assert aggregate_call is not None
    assert aggregate_call.args[2:5] == ("day", start, end)
    get_raw_sales.assert_not_awaited()


def test_auto_resolution_includes_20min_and_raw_for_recent_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_aggregates = AsyncMock(return_value=[])
    get_raw_sales = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "get_aggregates", get_aggregates)
    monkeypatch.setattr(service, "get_raw_sales", get_raw_sales)
    now = datetime.now(UTC)
    start = now - timedelta(hours=12)
    end = now

    asyncio.run(
        service.read_history(
            session=AsyncMock(),
            item_id="item-1",
            start=start,
            end=end,
            quality=None,
            resolution="auto",
        )
    )

    assert get_aggregates.await_count == 1
    assert get_aggregates.await_args.args[2] == "20min"
    get_raw_sales.assert_awaited_once()


def test_raw_history_points_include_additional_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_raw_sales = AsyncMock(
        return_value=[
            SimpleNamespace(
                sold_at=datetime(2026, 6, 29, 10, tzinfo=UTC),
                quality=3,
                price=Decimal("100"),
                amount=1,
                additional={"attributes": [{"definitionId": "concentration_aff"}]},
            )
        ]
    )
    monkeypatch.setattr(service, "get_raw_sales", get_raw_sales)

    result = asyncio.run(
        service.read_history(
            session=AsyncMock(),
            item_id="item-1",
            start=datetime(2026, 6, 29, tzinfo=UTC),
            end=datetime(2026, 6, 30, tzinfo=UTC),
            quality=None,
            resolution="raw",
        )
    )

    assert result.points[0].additional == {
        "attributes": [{"definitionId": "concentration_aff"}]
    }


def test_raw_history_passes_definition_filters_to_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_raw_sales = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "get_raw_sales", get_raw_sales)

    asyncio.run(
        service.read_history(
            session=AsyncMock(),
            item_id="module",
            start=datetime(2026, 6, 29, tzinfo=UTC),
            end=datetime(2026, 6, 30, tzinfo=UTC),
            quality=None,
            resolution="raw",
            definition_ids=["concentration", "concentration", ""],
        )
    )

    get_raw_sales.assert_awaited_once()
    assert get_raw_sales.await_args.args[5] == ("concentration",)


def test_aggregate_history_points_include_additional_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_aggregates = AsyncMock(
        return_value=[
            SimpleNamespace(
                bucket_start=datetime(2026, 6, 29, 10, tzinfo=UTC),
                quality=3,
                min_price=Decimal("100"),
                max_price=Decimal("100"),
                price_sum=Decimal("100"),
                weighted_price_sum=Decimal("100"),
                amount_sum=1,
                sale_count=1,
                additional={"attributes": [{"definitionId": "concentration_aff"}]},
            )
        ]
    )
    monkeypatch.setattr(service, "get_aggregates", get_aggregates)

    result = asyncio.run(
        service.read_history(
            session=AsyncMock(),
            item_id="item-1",
            start=datetime(2026, 6, 29, tzinfo=UTC),
            end=datetime(2026, 6, 30, tzinfo=UTC),
            quality=None,
            resolution="20min",
        )
    )

    assert result.points[0].additional == {
        "attributes": [{"definitionId": "concentration_aff"}]
    }


def test_aggregate_history_points_can_omit_additional_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_aggregates = AsyncMock(
        return_value=[
            SimpleNamespace(
                bucket_start=datetime(2026, 6, 29, 10, tzinfo=UTC),
                quality=3,
                min_price=Decimal("100"),
                max_price=Decimal("100"),
                price_sum=Decimal("100"),
                weighted_price_sum=Decimal("100"),
                amount_sum=1,
                sale_count=1,
                additional={"attributes": [{"definitionId": "concentration_aff"}]},
            )
        ]
    )
    monkeypatch.setattr(service, "get_aggregates", get_aggregates)

    result = asyncio.run(
        service.read_history(
            session=AsyncMock(),
            item_id="item-1",
            start=datetime(2026, 6, 29, tzinfo=UTC),
            end=datetime(2026, 6, 30, tzinfo=UTC),
            quality=None,
            resolution="hour",
            include_additional=False,
        )
    )

    assert result.points[0].additional is None


def test_aggregate_history_passes_definition_and_additional_key_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_aggregates = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "get_aggregates", get_aggregates)

    asyncio.run(
        service.read_history(
            session=AsyncMock(),
            item_id="module",
            start=datetime(2026, 6, 29, tzinfo=UTC),
            end=datetime(2026, 6, 30, tzinfo=UTC),
            quality=3,
            resolution="hour",
            definition_ids=["draw_time", "concentration"],
            additional_key="a" * 64,
        )
    )

    get_aggregates.assert_awaited_once()
    assert get_aggregates.await_args.args[6] == ("draw_time", "concentration")
    assert get_aggregates.await_args.args[7] == "a" * 64
