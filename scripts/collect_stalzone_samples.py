import argparse
import asyncio
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://stalzone.wiki/donttouch/api"


def read_item_ids(path: Path, limit: int | None) -> list[str]:
    ids = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    unique_ids = list(dict.fromkeys(ids))

    if limit is not None:
        return unique_ids[:limit]

    return unique_ids


async def fetch_json(
    client: httpx.AsyncClient,
    endpoint: str,
    item_id: str,
    region: str,
) -> tuple[int | None, Any]:
    try:
        response = await client.get(
            endpoint,
            params={"region": region, "id": item_id},
        )

        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {"raw_text": response.text}

        return response.status_code, body

    except httpx.HTTPError as exc:
        return None, {"error": type(exc).__name__, "message": str(exc)}


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def make_zip(source_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in source_dir.rglob("*"):
            if file_path == zip_path or not file_path.is_file():
                continue

            archive.write(
                filename=file_path,
                arcname=file_path.relative_to(source_dir),
            )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids-file", type=Path, required=True)
    parser.add_argument("--region", default="ru")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=2.5)
    parser.add_argument("--output-dir", type=Path, default=Path("data_samples/stalzone_raw"))
    args = parser.parse_args()

    internal_key = os.getenv("STALZONE_INTERNAL_KEY")
    if not internal_key:
        raise RuntimeError("Env STALZONE_INTERNAL_KEY is required")

    item_ids = read_item_ids(args.ids_file, args.limit)

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = args.output_dir / timestamp
    items_dir = run_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

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
        "X-Internal-Key": internal_key,
    }

    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers=headers,
        timeout=30.0,
    ) as client:
        for index, item_id in enumerate(item_ids, start=1):
            print(f"[{index}/{len(item_ids)}] {item_id}")

            item_dir = items_dir / item_id
            item_dir.mkdir(parents=True, exist_ok=True)

            history_status, history_body = await fetch_json(
                client=client,
                endpoint="/auction-history",
                item_id=item_id,
                region=args.region,
            )
            write_json(item_dir / "history.json", history_body)

            await asyncio.sleep(args.delay)

            lots_status, lots_body = await fetch_json(
                client=client,
                endpoint="/available-lots",
                item_id=item_id,
                region=args.region,
            )
            write_json(item_dir / "lots.json", lots_body)

            manifest["items"][item_id] = {
                "history_status": history_status,
                "lots_status": lots_status,
                "history_file": f"items/{item_id}/history.json",
                "lots_file": f"items/{item_id}/lots.json",
            }

            write_json(run_dir / "manifest.json", manifest)

            await asyncio.sleep(args.delay)

    zip_path = run_dir / f"stalzone_samples_{timestamp}.zip"
    make_zip(run_dir, zip_path)

    print(f"Done: {zip_path}")


if __name__ == "__main__":
    asyncio.run(main())