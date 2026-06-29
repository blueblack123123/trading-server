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
    assert _calculate_backfill_target(10_000) == 5_000
    assert _calculate_backfill_target(20_000) == 6_000
    assert _calculate_backfill_target(50_000) == 15_000
    assert _calculate_backfill_target(100_000) == 20_000


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
    assert offset == 5
    assert reached_end is False
    assert len(records) == 4
    assert all(record.sold_at.year == 2020 for record in records)
    assert [call.kwargs["offset"] for call in client.get_auction_history.await_args_list] == [0, 2]
