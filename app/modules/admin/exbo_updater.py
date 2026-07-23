import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

from app.clients.exbo_database import ExboDatabaseClient
from app.core.config import settings
from app.modules.admin.service import MarketItemsConfigService

logger = logging.getLogger(__name__)


class ExboDatabaseUpdater:
    def __init__(
        self,
        archive_url: str,
        database_path: str,
        timeout_seconds: int,
    ) -> None:
        self.archive_url = archive_url
        self.database_path = Path(database_path)
        self.timeout_seconds = timeout_seconds

    def update_once(self) -> int:
        target = self.database_path.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix=".stalzone-database-update-",
            dir=target.parent,
        ) as temp_dir:
            workspace = Path(temp_dir)
            archive_path = workspace / "database.zip"
            extract_path = workspace / "extract"

            self._download_archive(archive_path)
            source_root = self._extract_database_root(archive_path, extract_path)

            # Validate before replacing the mounted database directory.
            ExboDatabaseClient(str(source_root)).get_all_items()
            self._replace_database(source_root, target)

        synced_items = MarketItemsConfigService().sync_items()
        return len(synced_items)

    def _download_archive(self, archive_path: Path) -> None:
        logger.info("Downloading EXBO database from %s", self.archive_url)
        request = Request(self.archive_url, headers={"User-Agent": "trading-server"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            with archive_path.open("wb") as file:
                shutil.copyfileobj(response, file)

    def _extract_database_root(self, archive_path: Path, extract_path: Path) -> Path:
        extract_path.mkdir(parents=True, exist_ok=True)
        with ZipFile(archive_path) as archive:
            self._safe_extract(archive, extract_path)

        candidates = [extract_path, *[path for path in extract_path.iterdir() if path.is_dir()]]
        for candidate in candidates:
            if (candidate / "ru" / "items").is_dir():
                return candidate

        raise RuntimeError("Downloaded EXBO archive does not contain ru/items")

    @staticmethod
    def _safe_extract(archive: ZipFile, destination: Path) -> None:
        resolved_destination = destination.resolve()
        for member in archive.infolist():
            member_path = (destination / member.filename).resolve()
            if not member_path.is_relative_to(resolved_destination):
                raise RuntimeError(f"Unsafe path in EXBO archive: {member.filename}")

        archive.extractall(destination)

    @staticmethod
    def _replace_database(source_root: Path, target: Path) -> None:
        backup = target.with_name(f"{target.name}.previous")
        if backup.exists():
            shutil.rmtree(backup)

        target_was_moved = False
        try:
            if target.exists():
                target.rename(backup)
                target_was_moved = True
            source_root.rename(target)
        except Exception:
            if target_was_moved and backup.exists() and not target.exists():
                backup.rename(target)
            raise
        finally:
            if backup.exists():
                shutil.rmtree(backup)


async def run_forever() -> None:
    updater = ExboDatabaseUpdater(
        archive_url=settings.exbo_database_archive_url,
        database_path=settings.exbo_database_path,
        timeout_seconds=settings.exbo_database_update_timeout_seconds,
    )
    interval_seconds = max(1, settings.exbo_database_update_interval_seconds)

    while True:
        try:
            count = await asyncio.to_thread(updater.update_once)
            logger.info("EXBO database update complete: synced %s market items", count)
        except Exception as exc:
            logger.exception("EXBO database update failed: %s", exc)

        await asyncio.sleep(interval_seconds)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await run_forever()


if __name__ == "__main__":
    asyncio.run(main())
