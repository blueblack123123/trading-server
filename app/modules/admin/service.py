import json
from pathlib import Path

from app.clients.exbo_database import ExboDatabaseClient
from app.core.config import settings
from app.modules.admin.schemas import MarketItemConfig, MarketStatus


class MarketItemsConfigService:
    def __init__(self) -> None:
        self.config_path = Path(settings.market_items_config_path)
        self.exbo_database_client = ExboDatabaseClient(
            database_path=settings.exbo_database_path,
        )

    def get_config(self) -> list[MarketItemConfig]:
        if not self.config_path.exists():
            return []

        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        return [MarketItemConfig.model_validate(item) for item in data]

    def save_config(self, items: list[MarketItemConfig]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        data = [item.model_dump(mode="json") for item in items]

        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def sync_items(self) -> list[MarketItemConfig]:
        current_items = {item.id: item for item in self.get_config()}
        exbo_items = self.exbo_database_client.get_all_items()

        merged_items: list[MarketItemConfig] = []

        for item_id, item_name in exbo_items.items():
            old_item = current_items.get(item_id)

            merged_items.append(
                MarketItemConfig(
                    id=item_id,
                    name=item_name,
                    status=old_item.status if old_item else MarketStatus.AUTO,
                )
            )

        merged_items.sort(key=lambda item: item.name.lower())

        self.save_config(merged_items)

        return merged_items
