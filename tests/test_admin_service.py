import json

from app.core.config import settings
from app.modules.admin.schemas import MarketStatus
from app.modules.admin.service import MarketItemsConfigService


def test_sync_items_marks_new_items_ignored_by_default(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "market_items.json"
    monkeypatch.setattr(settings, "market_items_config_path", str(config_path))

    service = MarketItemsConfigService()
    monkeypatch.setattr(
        service.exbo_database_client,
        "get_all_items",
        lambda: {"new-id": "Новый предмет"},
    )

    items = service.sync_items()

    assert items[0].status == MarketStatus.IGNORE
    assert json.loads(config_path.read_text(encoding="utf-8")) == [
        {"id": "new-id", "name": "Новый предмет", "status": int(MarketStatus.IGNORE)}
    ]
