# Trading Server

FastAPI service and background worker for collecting STALZONE auction history.

## Run

1. Copy `.env.example` to `.env` and set `STALZONE_INTERNAL_KEY` and `ADMIN_KEY`.
2. Put the EXBO items database into `external/stalzone-database`.
3. Start the stack:

```shell
docker compose up --build
```

The stack starts PostgreSQL, applies Alembic migrations, runs the API on port 8000,
and starts one collector worker. The worker reads tracked items and their statuses from
`config/market_items.json`. Collection is disabled by default; set `COLLECTOR_ENABLED=true`
and restart `history-worker` after assigning item statuses.

Synchronize the item config after the EXBO database is mounted:

```shell
curl -X POST http://localhost:8000/api/v1/admin/sync-market-items \
  -H "X-Admin-Key: $ADMIN_KEY"
```

## History collection

The upstream limit defaults to five evenly spaced requests per minute. Status intervals:

- `HOT`: 60 seconds;
- `NORMAL`: 30 minutes;
- `RARE`: 8 hours;
- `AUTO`: starts at 24 hours and is promoted from observed sales activity;
- `IGNORE`: disabled.

Repeated upstream results are deduplicated by sale timestamp, price, amount, quality (`qlt`),
and canonical `additional` data. Raw sales are retained for 48 hours, hourly aggregates for
35 days, and daily aggregates indefinitely. Active lots are stored by a stable fingerprint;
disappeared lots remain available for 48 hours. Sales and lots share the same request budget.

Read stored history:

```text
GET /api/v1/items/{item_id}/history?from=...&to=...&resolution=auto&qlt=3
```

`resolution=auto` returns raw points for the last day, hourly points up to 30 days, and daily
points for older data. Explicit values are `raw`, `hour`, and `day`.

Read the last collected active lots without making an upstream request:

```text
GET /api/v1/items/{item_id}/lots?qlt=3
```

## Development checks

```shell
uv run pytest
uv run ruff check .
uv run mypy app
uv run alembic upgrade head --sql
```
