import asyncio
import logging
from datetime import UTC, datetime, timedelta
from math import ceil
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
from app.modules.history.repository import (
    compact_history,
    prune_hourly_aggregates,
    replace_hourly_aggregates,
    store_sales,
)

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
        self._live_rate_limiter = UniformRateLimiter(settings.history_live_requests_per_minute)
        self._backfill_rate_limiter = UniformRateLimiter(
            settings.history_backfill_requests_per_minute
        )

    async def run(self) -> None:
        logger.info(
            "Starting history worker with %s total requests/minute (live=%s, backfill=%s)",
            settings.stalzone_requests_per_minute,
            settings.history_live_requests_per_minute,
            settings.history_backfill_requests_per_minute,
        )
        if not settings.collector_enabled:
            logger.warning(
                "Collector is disabled. Set COLLECTOR_ENABLED=true and restart the worker."
            )
            while True:
                await self._sync_items(MarketItemsConfigService().get_config())
                await asyncio.sleep(300)

        await self._sync_items(MarketItemsConfigService().get_config())
        async with StalzoneClient() as client:
            await asyncio.gather(
                self._run_live_collection(client),
                self._run_backfill_collection(client),
            )

    async def _run_live_collection(self, client: StalzoneClient) -> None:
        next_config_refresh = monotonic() + 300.0
        next_compaction = 0.0
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

    async def _run_backfill_collection(self, client: StalzoneClient) -> None:
        while True:
            item = await self._claim_next_backfill()
            if item is None:
                await asyncio.sleep(settings.history_worker_idle_seconds)
                continue
            await self._backfill_history(client, item)

    async def _acquire_live_request(self) -> None:
        await self._live_rate_limiter.acquire()
        await self._rate_limiter.acquire()

    async def _acquire_backfill_request(self) -> None:
        await self._backfill_rate_limiter.acquire()
        await self._rate_limiter.acquire()

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

    async def _claim_next_backfill(self) -> MarketItem | None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            statement = (
                select(MarketItem, HistoryPollState)
                .join(HistoryPollState, HistoryPollState.item_id == MarketItem.id)
                .where(
                    MarketItem.configured_status != int(MarketStatus.IGNORE),
                    MarketItem.effective_status != int(MarketStatus.IGNORE),
                    HistoryPollState.backfill_complete.is_(False),
                    HistoryPollState.backfill_next_at <= now,
                )
                .order_by(HistoryPollState.backfill_next_at, MarketItem.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = (await session.execute(statement)).one_or_none()
            if row is None:
                return None
            item, state = row.tuple()
            state.backfill_next_at = now + timedelta(minutes=10)
            return item

    async def _poll_history(
        self,
        client: StalzoneClient,
        item: MarketItem,
        claimed_state: HistoryPollState,
    ) -> None:
        try:
            await self._acquire_live_request()
            first_payload = await client.get_auction_history(
                item_id=item.id,
                limit=settings.history_page_size,
            )
            first_page, total = _parse_history_page(item.id, first_payload)
            forced_status = await self._classify_special_auto(client, item, first_page, total)
            records = await self._collect_incremental(
                client,
                item.id,
                first_page,
                total,
                claimed_state,
            )

            await self._save_history_success(
                item=item,
                records=records,
                first_page=first_page,
                forced_status=forced_status,
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

        await self._acquire_live_request()
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
            await self._acquire_live_request()
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

    async def _save_history_success(
        self,
        item: MarketItem,
        records: list[SaleRecord],
        first_page: list[SaleRecord],
        forced_status: MarketStatus | None,
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

            state.last_polled_at = now
            state.last_success_at = now
            state.last_http_status = 200
            state.consecutive_errors = 0
            state.next_poll_at = now + _poll_interval(db_item)

        logger.info(
            "Polled history %s: received=%s inserted=%s status=%s next=%s",
            item.id,
            len(records),
            inserted,
            MarketStatus(db_item.effective_status).name,
            state.next_poll_at.isoformat(),
        )

    async def _backfill_history(self, client: StalzoneClient, item: MarketItem) -> None:
        try:
            records, total, target, offset, reached_end = await self._download_backfill(
                client,
                item.id,
            )
            complete_records = _exclude_partial_oldest_hour(records, reached_end)
            await self._save_backfill_success(
                item.id,
                complete_records,
                total,
                target,
                offset,
            )
        except httpx.HTTPStatusError as exc:
            await self._save_backfill_error(item.id, _retry_after_seconds(exc.response))
            logger.warning(
                "History backfill failed for %s: HTTP %s",
                item.id,
                exc.response.status_code,
            )
        except (httpx.HTTPError, ValueError, TypeError, RuntimeError) as exc:
            await self._save_backfill_error(item.id, 60)
            logger.exception("History backfill failed for %s: %s", item.id, exc)

    async def _download_backfill(
        self,
        client: StalzoneClient,
        item_id: str,
    ) -> tuple[list[SaleRecord], int, int, int, bool]:
        raw_boundary = _raw_history_boundary()
        old_records: dict[str, SaleRecord] = {}
        total = 0
        target = 0
        offset = 0
        processed_offset = 0
        reached_end = False

        while True:
            await self._acquire_backfill_request()
            payload = await client.get_auction_history(
                item_id=item_id,
                limit=settings.history_page_size,
                offset=offset,
            )
            page, page_total = _parse_history_page(item_id, payload)
            total = max(total, page_total)
            target = _calculate_backfill_target(total)
            processed_offset = min(total, max(processed_offset, offset + len(page)))
            for record in page:
                if record.sold_at < raw_boundary:
                    old_records[record.fingerprint] = record

            reached_end = not page or processed_offset >= total
            if reached_end or len(old_records) >= target:
                break

            overlap = min(settings.history_backfill_page_overlap, max(0, len(page) - 1))
            offset += len(page) - overlap

        return list(old_records.values()), total, target, processed_offset, reached_end

    async def _save_backfill_success(
        self,
        item_id: str,
        records: list[SaleRecord],
        total: int,
        target: int,
        offset: int,
    ) -> None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            replaced = await replace_hourly_aggregates(session, records)
            pruned = await prune_hourly_aggregates(
                session,
                settings.history_max_hourly_points_per_item,
                item_id,
            )
            state = await session.get(HistoryPollState, item_id, with_for_update=True)
            if state is None:
                return
            state.backfill_offset = offset
            state.backfill_target = target
            state.backfill_complete = True
            state.backfill_next_at = now

        logger.info(
            "Backfilled history %s: source=%s/%s target=%s hourly=%s pruned=%s",
            item_id,
            offset,
            total,
            target,
            replaced,
            pruned,
        )

    async def _save_backfill_error(self, item_id: str, retry_after_seconds: int) -> None:
        async with async_session_factory() as session, session.begin():
            state = await session.get(HistoryPollState, item_id, with_for_update=True)
            if state is not None:
                state.backfill_next_at = datetime.now(UTC) + timedelta(
                    seconds=max(1, retry_after_seconds)
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
            raw_count, lots_count, aggregate_count = await compact_history(
                session,
                raw_retention_hours=settings.history_raw_retention_hours,
                max_hourly_points_per_item=settings.history_max_hourly_points_per_item,
            )
        logger.info(
            "Compacted raw=%s inactive_lots=%s old_hourly=%s",
            raw_count,
            lots_count,
            aggregate_count,
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


def _calculate_backfill_target(total: int) -> int:
    if total <= 0:
        return 0
    fraction_target = ceil(total * settings.history_backfill_fraction)
    return min(
        total,
        settings.history_backfill_max_records,
        max(settings.history_backfill_min_records, fraction_target),
    )


def _raw_history_boundary(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    return (current - timedelta(hours=settings.history_raw_retention_hours)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )


def _exclude_partial_oldest_hour(
    records: list[SaleRecord],
    reached_end: bool,
) -> list[SaleRecord]:
    if reached_end or not records:
        return records
    oldest_hour = min(record.sold_at for record in records).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return [
        record
        for record in records
        if record.sold_at.replace(minute=0, second=0, microsecond=0) > oldest_hour
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
