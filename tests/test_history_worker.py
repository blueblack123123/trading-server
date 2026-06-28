from datetime import UTC, datetime

from app.modules.admin.schemas import MarketStatus
from app.modules.history.models import HistoryPollState, MarketItem
from app.modules.history.worker import (
    _filter_new_records,
    _parse_history_page,
    _parse_lots_page,
    _poll_interval,
    _should_backfill,
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


def test_extremely_rare_status_uses_weekly_interval_and_backfill() -> None:
    item = MarketItem(
        id="item-1",
        name="Item",
        configured_status=int(MarketStatus.EXTREMELY_RARE),
        effective_status=int(MarketStatus.EXTREMELY_RARE),
    )

    assert _should_backfill(item, None) is True
    assert _poll_interval(item).days == 7
