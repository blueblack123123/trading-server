import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.admin.schemas import MarketStatus
from app.modules.history.models import HistoryPollState, MarketItem
from app.modules.history.worker import (
    HistoryWorker,
    _calculate_backfill_target,
    _exclude_partial_oldest_hour,
    _filter_new_records,
    _parse_history_page,
    _parse_lots_page,
    _poll_interval,
    _update_auto_status,
)


def test_parse_history_page_returns_records_and_total() -> None:
    records, total = _parse_history_page(
        "item-1",
        {
            "total": 10,
            "prices": [
                {
                    "amount": 1,
                    "price": 100,
                    "time": "2026-06-28T10:11:12Z",
                    "additional": {"qlt": 3},
                }
            ],
        },
    )

    assert total == 10
    assert len(records) == 1
    assert records[0].quality == 3


def test_auto_status_requires_two_runs_to_promote() -> None:
    item = MarketItem(
        id="item-1",
        name="Item",
        configured_status=int(MarketStatus.AUTO),
        effective_status=int(MarketStatus.RARE),
    )
    state = HistoryPollState(
        item_id="item-1",
        activity_score=25,
        auto_candidate_runs=0,
        consecutive_errors=0,
        last_success_at=datetime(2026, 6, 28, tzinfo=UTC),
    )

    _update_auto_status(item, state)
    assert item.effective_status == int(MarketStatus.RARE)

    _update_auto_status(item, state)
    assert item.effective_status == int(MarketStatus.HOT)


def test_checkpoint_keeps_only_unseen_sales_at_same_time() -> None:
    records, _ = _parse_history_page(
        "item-1",
        {
            "prices": [
                {
                    "amount": 1,
                    "price": 100,
                    "time": "2026-06-28T10:11:12Z",
                    "additional": {"buyer": "first"},
                },
                {
                    "amount": 1,
                    "price": 100,
                    "time": "2026-06-28T10:11:12Z",
                    "additional": {"buyer": "second"},
                },
            ]
        },
    )

    new_records = _filter_new_records(
        records,
        records[0].sold_at,
        {records[0].fingerprint},
    )

    assert [record.fingerprint for record in new_records] == [records[1].fingerprint]


def test_parse_lots_page_returns_quality() -> None:
    records, total = _parse_lots_page(
        "item-1",
        {
            "total": 1,
            "lots": [
                {
                    "amount": 1,
                    "startPrice": 0,
                    "buyoutPrice": 100,
                    "startTime": "2026-06-28T10:00:00Z",
                    "endTime": "2026-06-30T10:00:00Z",
                    "additional": {"qlt": 2},
                }
            ],
        },
    )

    assert total == 1
    assert records[0].quality == 2


def test_collect_lot_snapshot_reads_pages_with_rate_limit() -> None:
    client = AsyncMock()
    client.get_available_lots.side_effect = [
        {
            "total": 2,
            "lots": [
                {
                    "amount": 1,
                    "startPrice": 10,
                    "currentPrice": 10,
                    "buyoutPrice": 100,
                    "startTime": "2026-06-28T10:00:00Z",
                    "endTime": "2026-06-29T10:00:00Z",
                    "additional": {"qlt": 2},
                }
            ],
        },
        {
            "total": 2,
            "lots": [
                {
                    "amount": 2,
                    "startPrice": 20,
                    "currentPrice": 20,
                    "buyoutPrice": 200,
                    "startTime": "2026-06-28T11:00:00Z",
                    "endTime": "2026-06-29T11:00:00Z",
                    "additional": {"qlt": 3},
                }
            ],
        },
    ]
    worker = HistoryWorker()
    worker._acquire_lot_request = AsyncMock()  # type: ignore[method-assign]

    records, total, complete = asyncio.run(worker._collect_lot_snapshot(client, "item-1"))

    assert total == 2
    assert complete is True
    assert [record.amount for record in records] == [1, 2]
    assert worker._acquire_lot_request.await_count == 2
    assert [call.kwargs["offset"] for call in client.get_available_lots.await_args_list] == [0, 1]


def test_extremely_rare_status_uses_weekly_interval() -> None:
    item = MarketItem(
        id="item-1",
        name="Item",
        configured_status=int(MarketStatus.EXTREMELY_RARE),
        effective_status=int(MarketStatus.EXTREMELY_RARE),
    )

    assert _poll_interval(item).days == 7


def test_backfill_target_applies_floor_fraction_and_cap() -> None:
    assert _calculate_backfill_target(3_000) == 3_000
    assert _calculate_backfill_target(10_000) == 10_000
    assert _calculate_backfill_target(20_000) == 20_000
    assert _calculate_backfill_target(50_000) == 40_000
    assert _calculate_backfill_target(100_000) == 40_000


def test_artifact_backfill_target_uses_aggregate_capacity_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "history_backfill_min_records", 5_000)
    monkeypatch.setattr(settings, "history_backfill_fraction", 1.0)
    monkeypatch.setattr(settings, "history_backfill_max_records", 40_000)
    monkeypatch.setattr(settings, "history_max_aggregate_points_per_item", 40_000)
    monkeypatch.setattr(
        "app.modules.history.worker._is_artifact_item_id",
        lambda item_id: item_id == "4lml",
    )

    assert _calculate_backfill_target(728_975, "4lml") == 728_975
    assert _calculate_backfill_target(900_000, "4lml") == 800_000
    assert _calculate_backfill_target(728_975, "not-artifact") == 40_000


def test_weapon_module_backfill_target_uses_aggregate_capacity_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "history_backfill_min_records", 5_000)
    monkeypatch.setattr(settings, "history_backfill_fraction", 1.0)
    monkeypatch.setattr(settings, "history_backfill_max_records", 40_000)
    monkeypatch.setattr(settings, "history_max_aggregate_points_per_item", 40_000)
    monkeypatch.setattr(settings, "history_max_module_aggregate_points_per_item", 500_000)
    monkeypatch.setattr(
        "app.modules.history.worker._is_artifact_item_id",
        lambda item_id: False,
    )
    monkeypatch.setattr(
        "app.modules.history.worker._is_weapon_module_item_id",
        lambda item_id: item_id == "1pyq",
    )

    assert _calculate_backfill_target(500_000, "1pyq") == 500_000
    assert _calculate_backfill_target(900_000, "1pyq") == 900_000
    assert _calculate_backfill_target(500_000, "not-module") == 40_000


def test_backfill_rate_uses_maximum_when_live_backlog_is_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "history_backfill_requests_per_minute", 30)
    monkeypatch.setattr(settings, "history_backfill_max_requests_per_minute", 60)
    monkeypatch.setattr(settings, "history_backfill_live_backlog_threshold", 5)
    worker = HistoryWorker()
    worker._count_due_live_history = AsyncMock(return_value=2)  # type: ignore[method-assign]

    assert asyncio.run(worker._select_backfill_rate()) == 60


def test_backfill_rate_uses_minimum_when_live_backlog_is_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "history_backfill_requests_per_minute", 30)
    monkeypatch.setattr(settings, "history_backfill_max_requests_per_minute", 60)
    monkeypatch.setattr(settings, "history_backfill_live_backlog_threshold", 5)
    worker = HistoryWorker()
    worker._count_due_live_history = AsyncMock(return_value=5)  # type: ignore[method-assign]

    assert asyncio.run(worker._select_backfill_rate()) == 30


def test_partial_oldest_hour_is_not_written() -> None:
    records, _ = _parse_history_page(
        "item-1",
        {
            "prices": [
                {
                    "amount": 1,
                    "price": 100,
                    "time": "2026-06-20T11:30:00Z",
                },
                {
                    "amount": 1,
                    "price": 90,
                    "time": "2026-06-20T10:59:00Z",
                },
            ]
        },
    )

    complete = _exclude_partial_oldest_hour(records, reached_end=False)

    assert [record.sold_at.hour for record in complete] == [11]
    assert _exclude_partial_oldest_hour(records, reached_end=True) == records


def test_backfill_excludes_recent_records_and_overlaps_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "history_backfill_min_records", 3)
    monkeypatch.setattr(settings, "history_backfill_max_records", 3)
    monkeypatch.setattr(settings, "history_backfill_fraction", 0.30)
    monkeypatch.setattr(settings, "history_page_size", 3)
    monkeypatch.setattr(settings, "history_backfill_page_overlap", 1)
    client = AsyncMock()
    client.get_auction_history.side_effect = [
        {
            "total": 6,
            "prices": [
                {"amount": 1, "price": 200, "time": "2099-01-01T00:00:00Z"},
                {"amount": 1, "price": 100, "time": "2020-01-01T03:00:00Z"},
                {"amount": 1, "price": 90, "time": "2020-01-01T02:00:00Z"},
            ],
        },
        {
            "total": 6,
            "prices": [
                {"amount": 1, "price": 90, "time": "2020-01-01T02:00:00Z"},
                {"amount": 1, "price": 80, "time": "2020-01-01T01:00:00Z"},
                {"amount": 1, "price": 70, "time": "2020-01-01T00:00:00Z"},
            ],
        },
    ]
    worker = HistoryWorker()
    worker._acquire_backfill_request = AsyncMock()  # type: ignore[method-assign]

    records, total, target, offset, reached_end = asyncio.run(
        worker._download_backfill(client, "item-1")
    )

    assert total == 6
    assert target == 3
    assert offset == 3
    assert reached_end is False
    assert len(records) == 2
    assert all(record.sold_at.year == 2020 for record in records)
    assert [call.kwargs["offset"] for call in client.get_auction_history.await_args_list] == [0]
