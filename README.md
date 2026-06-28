# Trading Server

FastAPI service and background worker for collecting STALZONE auction history.

## Run

1. Copy `.env.example` to `.env` and set `STALZONE_CLIENT_ID`,
   `STALZONE_CLIENT_SECRET`, and `ADMIN_KEY`.
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

The worker uses the official OAuth API and defaults to 180 evenly spaced auction requests
per minute, leaving capacity for frontend requests. Status intervals:

- `HOT`: 60 seconds;
- `NORMAL`: 1 hour;
- `RARE`: 12 hours;
- `EXTREMELY_RARE`: 7 days;
- `AUTO`: classified on the first history response, then uses its effective status interval;
- `IGNORE`: disabled.

For `AUTO`, an empty history immediately results in `IGNORE`. If the latest sale is older
than 90 days and there are no active lots, the effective status becomes `EXTREMELY_RARE`
and the worker incrementally backfills the complete sale history.

Repeated upstream results are deduplicated by sale timestamp, price, amount, quality (`qlt`),
and canonical `additional` data. Raw sales are retained for 48 hours, hourly aggregates for
35 days, and daily aggregates indefinitely. The worker only collects sales. Active lots are
requested from the official API on demand and cached for 15 seconds.

Read stored history:

```text
GET /api/v1/items/{item_id}/history?from=...&to=...&resolution=auto&qlt=3
```

`resolution=auto` returns raw points for the last day, hourly points up to 30 days, and daily
points for older data. Explicit values are `raw`, `hour`, and `day`.

Fetch active lots on demand (subsequent requests use the short cache):

```text
GET /api/v1/items/{item_id}/lots
```

The lots endpoint always returns the complete listing without server-side filters. Each lot
contains prices, quality, full additional data, auction start/end time, and snapshot timestamps;
the frontend is responsible for filtering and sorting.

## Development checks

```shell
uv run pytest
uv run ruff check .
uv run mypy app
uv run alembic upgrade head --sql
```
