from pathlib import Path

import pytest

from app.clients.exbo_database import ExboDatabaseClient


def test_missing_database_does_not_look_like_empty_database(tmp_path: Path) -> None:
    client = ExboDatabaseClient(str(tmp_path / "missing"))

    with pytest.raises(FileNotFoundError):
        client.get_all_items()
