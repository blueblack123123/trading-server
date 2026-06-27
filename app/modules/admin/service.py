import json
from pathlib import Path

import httpx

from app.core.config import settings
from app.modules.admin.schemas import MarketItemConfig, MarketStatus

EXBO_ITEMS_TREE_URL = (
    "https://api.github.com/repos/EXBO-Studio/stalzone-database/git/trees/main?recursive=1"
)
EXBO_RAW_ITEM_URL = "https://raw.githubusercontent.com/EXBO-Studio/stalzone-database/main/{path}"


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
        data = [item.model_dump(mode="json") for item in items]
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def sync_items(self) -> list[MarketItemConfig]:
        old_items = {item.id: item for item in self.get_config()}
        exbo_items = await self._load_exbo_items()

        merged_items: list[MarketItemConfig] = []

        for item_id, name in sorted(exbo_items.items(), key=lambda item: item[1].lower()):
            old_item = old_items.get(item_id)

            merged_items.append(
                MarketItemConfig(
                    id=item_id,
                    name=name,
                    status=old_item.status if old_item else MarketStatus.AUTO,
                )
            )

        self.save_config(merged_items)
        return merged_items

    async def _load_exbo_items(self) -> dict[str, str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            tree_response = await client.get(EXBO_ITEMS_TREE_URL)
            tree_response.raise_for_status()
            tree = tree_response.json()["tree"]

            item_paths = [
                node["path"]
                for node in tree
                if node.get("path", "").startswith("ru/items/")
                and node.get("path", "").endswith(".json")
            ]

            result: dict[str, str] = {}

            for path in item_paths:
                item_id = Path(path).stem

                item_response = await client.get(EXBO_RAW_ITEM_URL.format(path=path))
                item_response.raise_for_status()
                item_data = item_response.json()

                name = self._extract_name(item_data=item_data, fallback=item_id)
                result[item_id] = name

            return result

    def _extract_name(self, item_data: dict, fallback: str) -> str:
        name = item_data.get("name")

        if isinstance(name, str):
            return name

        if isinstance(name, dict):
            for key in ("ru", "text", "value"):
                value = name.get(key)
                if isinstance(value, str):
                    return value

        return fallback