import json
from pathlib import Path


class ExboDatabaseClient:
    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)
        self.items_path = self.database_path / "ru" / "items"

    def get_all_items(self) -> dict[str, str]:
        result: dict[str, str] = {}

        for path in self.items_path.rglob("*.json"):
            item_data = json.loads(path.read_text(encoding="utf-8"))

            item_id = path.stem
            item_name = self._extract_name(item_data, item_id)

            result[item_id] = item_name

        return result

    @staticmethod
    def _extract_name(item_data: dict, fallback: str) -> str:
        name = item_data.get("name")

        if isinstance(name, str):
            return name

        if isinstance(name, dict):
            lines = name.get("lines")
            if isinstance(lines, dict):
                value = lines.get("ru")
                if isinstance(value, str):
                    return value

            for key in ("ru", "text", "value"):
                value = name.get(key)
                if isinstance(value, str):
                    return value

        return fallback