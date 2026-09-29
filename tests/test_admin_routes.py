from app.core.config import settings
from app.modules.admin import routes
from app.modules.admin.schemas import HistoryBackfillStatus


def test_selected_backfill_rate_uses_maximum_when_live_backlog_is_low(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "history_backfill_requests_per_minute", 30)
    monkeypatch.setattr(settings, "history_backfill_max_requests_per_minute", 60)
    monkeypatch.setattr(settings, "history_backfill_live_backlog_threshold", 5)

    assert routes._selected_backfill_rate(4) == 60


def test_selected_backfill_rate_uses_minimum_when_live_backlog_is_high(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "history_backfill_requests_per_minute", 30)
    monkeypatch.setattr(settings, "history_backfill_max_requests_per_minute", 60)
    monkeypatch.setattr(settings, "history_backfill_live_backlog_threshold", 5)

    assert routes._selected_backfill_rate(5) == 30


def test_request_budget_sums_worker_limits(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stalzone_requests_per_minute", 200)
    monkeypatch.setattr(settings, "history_live_requests_per_minute", 150)
    monkeypatch.setattr(settings, "lots_collection_enabled", True)
    monkeypatch.setattr(settings, "lots_requests_per_minute", 20)
    backfill = HistoryBackfillStatus(
        tracked_items=10,
        complete_items=5,
        pending_items=5,
        due_live_items=0,
        selected_backfill_requests_per_minute=60,
        oldest_backfill_next_at=None,
        newest_backfill_success_at=None,
        total_backfill_offset=0,
        total_backfill_target=0,
    )

    budget = routes._read_request_budget(backfill)

    assert budget.configured_workers_per_minute == 230
    assert budget.stalzone_limit_per_minute == 200
    assert budget.spare_per_minute == 0
    assert budget.capped_by_global_limiter is True
