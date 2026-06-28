import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic, time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.clients.stalzone import StalzoneClient
from app.core.config import settings
from app.db.session import async_session_factory
from app.modules.admin.schemas import MarketItemConfig, MarketStatus
from app.modules.admin.service import MarketItemsConfigService
from app.modules.history.domain import LotRecord, SaleRecord, parse_lot, parse_sale
from app.modules.history.models import HistoryPollState, LotPollState, MarketItem
from app.modules.history.repository import compact_history, store_sales

logger = logging.getLogger(__name__)


class UniformRateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self._interval = 60.0 / requests_per_minute
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            delay = max(0.0, self._next_allowed - monotonic())
            if delay:
                await asyncio.sleep(delay)
            self._next_allowed = monotonic() + self._interval


class HistoryWorker:
    def __init__(self) -> None:
        self._rate_limiter = UniformRateLimiter(settings.stalzone_requests_per_minute)

    async def run(self) -> None:
        logger.info(
            "Starting history worker with %s requests/minute",
            settings.stalzone_requests_per_minute,
        )
        if not settings.collector_enabled:
            logger.warning(
                "Collector is disabled. Set COLLECTOR_ENABLED=true and restart the worker."
            )
            while True:
                await self._sync_items(MarketItemsConfigService().get_config())
                await asyncio.sleep(300)

        next_config_refresh = 0.0
        next_compaction = 0.0
        async with StalzoneClient() as client:
            while True:
                now_monotonic = monotonic()
                if now_monotonic >= next_config_refresh:
                    await self._sync_items(MarketItemsConfigService().get_config())
                    next_config_refresh = now_monotonic + 300.0
                if now_monotonic >= next_compaction:
                    await self._compact()
                    next_compaction = now_monotonic + 3600.0

                claimed = await self._claim_next_history()
                if claimed is None:
                    await asyncio.sleep(settings.history_worker_idle_seconds)
                    continue
                await self._poll_history(client, *claimed)

    async def _sync_items(self, items: list[MarketItemConfig]) -> None:
        if not items:
            logger.warning("Market items config is empty; worker has nothing to poll")
            return

        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            for item in items:
                effective = MarketStatus.RARE if item.status == MarketStatus.AUTO else item.status
                statement = insert(MarketItem).values(
                    id=item.id,
                    name=item.name,
                    configured_status=int(item.status),
                    effective_status=int(effective),
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[MarketItem.id],
                        set_={
                            "name": statement.excluded.name,
                            "configured_status": statement.excluded.configured_status,
                            "effective_status": (
                                statement.excluded.effective_status
                                if item.status != MarketStatus.AUTO
                                else MarketItem.effective_status
                            ),
                        },
                    )
                )
                history_statement = insert(HistoryPollState).values(
                    item_id=item.id,
                    next_poll_at=now,
                )
                await session.execute(
                    history_statement.on_conflict_do_nothing(
                        index_elements=[HistoryPollState.item_id]
                    )
                )
                lot_statement = insert(LotPollState).values(item_id=item.id, next_poll_at=now)
                await session.execute(
                    lot_statement.on_conflict_do_nothing(index_elements=[LotPollState.item_id])
                )

    async def _claim_next_history(self) -> tuple[MarketItem, HistoryPollState] | None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            statement = (
                select(MarketItem, HistoryPollState)
                .join(HistoryPollState, HistoryPollState.item_id == MarketItem.id)
                .where(
                    MarketItem.configured_status != int(MarketStatus.IGNORE),
                    MarketItem.effective_status != int(MarketStatus.IGNORE),
                    HistoryPollState.next_poll_at <= now,
                )
                .order_by(
                    HistoryPollState.next_poll_at,
                    MarketItem.effective_status,
                    MarketItem.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = (await session.execute(statement)).one_or_none()
            if row is None:
                return None
            item, state = row.tuple()
            state.next_poll_at = now + timedelta(minutes=5)
            return item, state

    async def _poll_history(
        self,
        client: StalzoneClient,
        item: MarketItem,
        claimed_state: HistoryPollState,
    ) -> None:
        try:
            await self._rate_limiter.acquire()
            first_payload = await client.get_auction_history(
                item_id=item.id,
                limit=settings.history_page_size,
            )
            first_page, total = _parse_history_page(item.id, first_payload)
            forced_status = await self._classify_special_auto(client, item, first_page, total)
            should_backfill = _should_backfill(item, forced_status)

            if should_backfill:
                records, backfill_offset, backfill_complete = await self._collect_backfill(
                    client,
                    item.id,
                    first_page,
                    total,
                    claimed_state,
                )
            else:
                records = await self._collect_incremental(
                    client,
                    item.id,
                    first_page,
                    total,
                    claimed_state,
                )
                backfill_offset = claimed_state.backfill_offset
                backfill_complete = claimed_state.backfill_complete

            await self._save_history_success(
                item=item,
                records=records,
                first_page=first_page,
                forced_status=forced_status,
                backfill_offset=backfill_offset,
                backfill_complete=backfill_complete,
            )
        except httpx.HTTPStatusError as exc:
            await self._save_history_error(
                item.id,
                exc.response.status_code,
                _retry_after_seconds(exc.response),
            )
            logger.warning(
                "History request failed for %s: HTTP %s",
                item.id,
                exc.response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError, RuntimeError) as exc:
            await self._save_history_error(item.id, None, 60)
            logger.exception("History poll failed for %s: %s", item.id, exc)

    async def _classify_special_auto(
        self,
        client: StalzoneClient,
        item: MarketItem,
        first_page: list[SaleRecord],
        total: int,
    ) -> MarketStatus | None:
        if item.configured_status != int(MarketStatus.AUTO):
            return None
        if total == 0 or not first_page:
            return MarketStatus.IGNORE

        newest = max(record.sold_at for record in first_page)
        if newest >= datetime.now(UTC) - timedelta(days=90):
            if item.effective_status == int(MarketStatus.EXTREMELY_RARE):
                return MarketStatus.RARE
            return None

        await self._rate_limiter.acquire()
        lots_payload = await client.get_available_lots(item_id=item.id, limit=1)
        _, total_lots = _parse_lots_page(item.id, lots_payload)
        return MarketStatus.EXTREMELY_RARE if total_lots == 0 else MarketStatus.RARE

    async def _collect_incremental(
        self,
        client: StalzoneClient,
        item_id: str,
        first_page: list[SaleRecord],
        total: int,
        state: HistoryPollState,
    ) -> list[SaleRecord]:
        records = list(first_page)
        checkpoint = state.latest_sale_at
        offset = len(first_page)
        page_count = 1
        while (
            checkpoint is not None
            and records
            and min(record.sold_at for record in records[-len(first_page) :]) > checkpoint
            and offset < total
            and page_count < settings.history_incremental_max_pages
        ):
            await self._rate_limiter.acquire()
            payload = await client.get_auction_history(
                item_id=item_id,
                limit=settings.history_page_size,
                offset=offset,
            )
            page, _ = _parse_history_page(item_id, payload)
            if not page:
                break
            records.extend(page)
            offset += len(page)
            page_count += 1

        return _filter_new_records(
            records,
            checkpoint,
            set(state.latest_sale_fingerprints),
        )

    async def _collect_backfill(
        self,
        client: StalzoneClient,
        item_id: str,
        first_page: list[SaleRecord],
        total: int,
        state: HistoryPollState,
    ) -> tuple[list[SaleRecord], int, bool]:
        records = _filter_new_records(
            first_page,
            state.latest_sale_at,
            set(state.latest_sale_fingerprints),
        )
        if state.backfill_complete:
            return records, state.backfill_offset, True

        offset = max(len(first_page), state.backfill_offset)
        for _ in range(settings.history_backfill_pages_per_poll):
            if offset >= total:
                break
            await self._rate_limiter.acquire()
            payload = await client.get_auction_history(
                item_id=item_id,
                limit=settings.history_page_size,
                offset=offset,
            )
            page, _ = _parse_history_page(item_id, payload)
            if not page:
                break
            records.extend(page)
            offset += len(page)

        unique_records = list({record.fingerprint: record for record in records}.values())
        return unique_records, offset, offset >= total

    async def _save_history_success(
        self,
        item: MarketItem,
        records: list[SaleRecord],
        first_page: list[SaleRecord],
        forced_status: MarketStatus | None,
        backfill_offset: int,
        backfill_complete: bool,
    ) -> None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            inserted = await store_sales(session, records)
            state = await session.get(HistoryPollState, item.id, with_for_update=True)
            db_item = await session.get(MarketItem, item.id, with_for_update=True)
            if state is None or db_item is None:
                return

            recent_sales = sum(
                1 for record in first_page if record.sold_at >= now - timedelta(hours=1)
            )
            state.activity_score = (
                float(recent_sales)
                if state.last_success_at is None
                else state.activity_score * 0.7 + recent_sales * 0.3
            )
            if db_item.configured_status == int(MarketStatus.AUTO):
                if forced_status is not None:
                    db_item.effective_status = int(forced_status)
                    state.auto_candidate_status = None
                    state.auto_candidate_runs = 0
                else:
                    _update_auto_status(db_item, state)

            if first_page:
                newest = max(record.sold_at for record in first_page)
                newest_fingerprints = {
                    record.fingerprint for record in first_page if record.sold_at == newest
                }
                if state.latest_sale_at is None or newest > state.latest_sale_at:
                    state.latest_sale_at = newest
                    state.latest_sale_fingerprints = sorted(newest_fingerprints)
                elif newest == state.latest_sale_at:
                    state.latest_sale_fingerprints = sorted(
                        set(state.latest_sale_fingerprints) | newest_fingerprints
                    )

            state.backfill_offset = backfill_offset
            state.backfill_complete = backfill_complete
            state.last_polled_at = now
            state.last_success_at = now
            state.last_http_status = 200
            state.consecutive_errors = 0
            state.next_poll_at = (
                now + timedelta(minutes=1)
                if _is_extremely_rare(db_item) and not backfill_complete
                else now + _poll_interval(db_item)
            )

        logger.info(
            "Polled history %s: received=%s inserted=%s status=%s backfill=%s/%s next=%s",
            item.id,
            len(records),
            inserted,
            MarketStatus(db_item.effective_status).name,
            backfill_offset,
            "done" if backfill_complete else "pending",
            state.next_poll_at.isoformat(),
        )

    async def _save_history_error(
        self,
        item_id: str,
        http_status: int | None,
        retry_after_seconds: int,
    ) -> None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            state = await session.get(HistoryPollState, item_id, with_for_update=True)
            if state is None:
                return
            state.last_polled_at = now
            state.last_http_status = http_status
            state.consecutive_errors += 1
            backoff = max(retry_after_seconds, min(3600, 60 * 2**state.consecutive_errors))
            state.next_poll_at = now + timedelta(seconds=backoff)

    async def _compact(self) -> None:
        async with async_session_factory() as session, session.begin():
            raw_count, hourly_count, lots_count = await compact_history(session)
        logger.info(
            "Compacted raw=%s hourly=%s inactive_lots=%s",
            raw_count,
            hourly_count,
            lots_count,
        )


def _parse_history_page(item_id: str, payload: Any) -> tuple[list[SaleRecord], int]:
    if not isinstance(payload, dict):
        raise ValueError("history response must be an object")
    raw_prices = payload.get("prices", [])
    if not isinstance(raw_prices, list):
        raise ValueError("history response prices must be a list")
    records = [parse_sale(item_id, row) for row in raw_prices if isinstance(row, dict)]
    return records, _parse_total(payload.get("total"), len(records))


def _parse_lots_page(item_id: str, payload: Any) -> tuple[list[LotRecord], int]:
    if not isinstance(payload, dict):
        raise ValueError("lots response must be an object")
    raw_lots = payload.get("lots", [])
    if not isinstance(raw_lots, list):
        raise ValueError("lots response lots must be a list")
    records = [parse_lot(item_id, row) for row in raw_lots if isinstance(row, dict)]
    return records, _parse_total(payload.get("total"), len(records))


def _parse_total(value: object, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return fallback
    return max(int(value), fallback)


def _filter_new_records(
    records: list[SaleRecord],
    checkpoint: datetime | None,
    checkpoint_fingerprints: set[str],
) -> list[SaleRecord]:
    if checkpoint is None:
        return records
    return [
        record
        for record in records
        if record.sold_at > checkpoint
        or (record.sold_at == checkpoint and record.fingerprint not in checkpoint_fingerprints)
    ]


def _update_auto_status(item: MarketItem, state: HistoryPollState) -> None:
    if state.activity_score >= 20:
        desired = MarketStatus.HOT
    elif state.activity_score >= 1:
        desired = MarketStatus.NORMAL
    else:
        desired = MarketStatus.RARE

    if state.last_success_at is None:
        item.effective_status = int(desired)
        state.auto_candidate_status = None
        state.auto_candidate_runs = 0
        return

    if state.auto_candidate_status == int(desired):
        state.auto_candidate_runs += 1
    else:
        state.auto_candidate_status = int(desired)
        state.auto_candidate_runs = 1
    required_runs = 2 if int(desired) < item.effective_status else 3
    if state.auto_candidate_runs >= required_runs:
        item.effective_status = int(desired)
        state.auto_candidate_runs = 0


def _should_backfill(item: MarketItem, forced_status: MarketStatus | None) -> bool:
    return forced_status == MarketStatus.EXTREMELY_RARE or _is_extremely_rare(item)


def _is_extremely_rare(item: MarketItem) -> bool:
    return item.configured_status == int(
        MarketStatus.EXTREMELY_RARE
    ) or item.effective_status == int(MarketStatus.EXTREMELY_RARE)


def _poll_interval(item: MarketItem) -> timedelta:
    effective = MarketStatus(item.effective_status)
    intervals = {
        MarketStatus.HOT: settings.history_hot_interval_seconds,
        MarketStatus.NORMAL: settings.history_normal_interval_seconds,
        MarketStatus.RARE: settings.history_rare_interval_seconds,
        MarketStatus.EXTREMELY_RARE: settings.history_extremely_rare_interval_seconds,
        MarketStatus.IGNORE: settings.history_extremely_rare_interval_seconds,
    }
    return timedelta(seconds=intervals[effective])


def _retry_after_seconds(response: httpx.Response) -> int:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(1, int(retry_after))
        except ValueError:
            pass
    reset = response.headers.get("X-RateLimit-Reset")
    if reset is not None:
        try:
            return max(1, int(int(reset) / 1000 - time()))
        except ValueError:
            pass
    return 60


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await HistoryWorker().run()


if __name__ == "__main__":
    asyncio.run(main())
