from app.core.config import settings
from app.modules.admin import routes


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
