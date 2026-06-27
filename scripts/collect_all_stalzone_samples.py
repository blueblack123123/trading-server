import argparse
import asyncio
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

GITHUB_TREE_URL = "https://api.github.com/repos/EXBO-Studio/stalzone-database/git/trees/main?recursive=1"
RAW_ITEM_URL = "https://raw.githubusercontent.com/EXBO-Studio/stalzone-database/main/ru/items/{item_id}.json"
STALZONE_API = "https://stalzone.wiki/donttouch/api"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def zip_dir(source_dir: Path) -> Path:
    zip_path = source_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in source_dir.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source_dir))
    return zip_path


async def get_item_ids() -> list[str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(GITHUB_TREE_URL)
        response.raise_for_status()
        tree = response.json()["tree"]

    ids = []
    for node in tree:
        path = node.get("path", "")
        if path.startswith("ru/items/") and path.endswith(".json"):
            ids.append(Path(path).stem)

    return sorted(set(ids))


async def fetch_stalzone(
    client: httpx.AsyncClient,
    endpoint: str,
    item_id: str,
    region: str,
) -> dict[str, Any]:
    try:
        response = await client.get(endpoint, params={"region": region, "id": item_id})
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {"raw_text": response.text}

        return {"status_code": response.status_code, "body": body}
    except Exception as exc:
        return {"status_code": None, "error": type(exc).__name__, "message": str(exc)}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="ru")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    key = os.getenv("STALZONE_INTERNAL_KEY")
    if not key:
        raise RuntimeError("STALZONE_INTERNAL_KEY is required")

    item_ids = await get_item_ids()
    if args.limit:
        item_ids = item_ids[: args.limit]

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = Path("data_samples") / f"stalzone_{timestamp}"

    manifest: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "region": args.region,
        "items_count": len(item_ids),
        "delay_seconds": args.delay,
        "items": {},
    }

    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
        "X-Internal-Key": key,
    }

    async with httpx.AsyncClient(base_url=STALZONE_API, headers=headers, timeout=30.0) as client:
        for index, item_id in enumerate(item_ids, start=1):
            print(f"[{index}/{len(item_ids)}] {item_id}")

            history = await fetch_stalzone(client, "/auction-history", item_id, args.region)
            write_json(run_dir / "history" / f"{item_id}.json", history)

            await asyncio.sleep(args.delay)

            lots = await fetch_stalzone(client, "/available-lots", item_id, args.region)
            write_json(run_dir / "lots" / f"{item_id}.json", lots)

            manifest["items"][item_id] = {
                "history_status": history.get("status_code"),
                "lots_status": lots.get("status_code"),
                "history_count": len(history.get("body", {}).get("prices", []))
                if isinstance(history.get("body"), dict)
                else None,
                "lots_count": len(lots.get("body", {}).get("lots", []))
                if isinstance(lots.get("body"), dict)
                else None,
            }

            write_json(run_dir / "manifest.json", manifest)

            await asyncio.sleep(args.delay)

    print(f"Done: {zip_dir(run_dir)}")


if __name__ == "__main__":
    asyncio.run(main())