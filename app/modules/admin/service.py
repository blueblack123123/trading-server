import json
from pathlib import Path

from app.core.config import settings
from app.modules.admin.schemas import MarketItemConfig


class MarketItemsConfigService:
    def __init__(self) -> None:
        self.path = Path(settings.market_items_config_path)

    def get_config(self) -> list[MarketItemConfig]:
        if not self.path.exists():
            return []

        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [MarketItemConfig.model_validate(item) for item in data]

    def save_config(self, items: list[MarketItemConfig]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        data = [item.model_dump() for item in items]
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )