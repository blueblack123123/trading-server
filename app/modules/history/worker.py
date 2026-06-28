import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
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
from app.modules.history.repository import compact_history, store_lots, store_sales

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
            now = monotonic()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                await asyncio.sleep(delay)
            self._next_allowed = monotonic() + self._interval


class HistoryWorker:
    def __init__(self) -> None:
        self._rate_limiter = UniformRateLimiter(settings.stalzone_requests_per_minute)
        self._prefer_lots = False

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

                history_claimed: tuple[MarketItem, HistoryPollState] | None = None
                lots_claimed: tuple[MarketItem, LotPollState] | None = None
                if self._prefer_lots:
                    lots_claimed = await self._claim_next_lots()
                    if lots_claimed is None:
                        history_claimed = await self._claim_next_history()
                else:
                    history_claimed = await self._claim_next_history()
                    if history_claimed is None:
                        lots_claimed = await self._claim_next_lots()

                if history_claimed is None and lots_claimed is None:
                    await asyncio.sleep(settings.history_worker_idle_seconds)
                    continue

                self._prefer_lots = not self._prefer_lots
                if history_claimed is not None:
                    history_item, history_state = history_claimed
                    await self._poll_history(client, history_item, history_state)
                elif lots_claimed is not None:
                    lots_item, lots_state = lots_claimed
                    await self._poll_lots(client, lots_item, lots_state)

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
                poll_statement = insert(HistoryPollState).values(
                    item_id=item.id,
                    next_poll_at=now,
                )
                await session.execute(
                    poll_statement.on_conflict_do_nothing(index_elements=[HistoryPollState.item_id])
                )
                lot_poll_statement = insert(LotPollState).values(
                    item_id=item.id,
                    next_poll_at=now,
                )
                await session.execute(
                    lot_poll_statement.on_conflict_do_nothing(index_elements=[LotPollState.item_id])
                )

    async def _claim_next_history(self) -> tuple[MarketItem, HistoryPollState] | None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            statement = (
                select(MarketItem, HistoryPollState)
                .join(HistoryPollState, HistoryPollState.item_id == MarketItem.id)
                .where(
                    MarketItem.configured_status != int(MarketStatus.IGNORE),
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

    async def _claim_next_lots(self) -> tuple[MarketItem, LotPollState] | None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            statement = (
                select(MarketItem, LotPollState)
                .join(LotPollState, LotPollState.item_id == MarketItem.id)
                .where(
                    MarketItem.configured_status != int(MarketStatus.IGNORE),
                    LotPollState.next_poll_at <= now,
                )
                .order_by(
                    LotPollState.next_poll_at,
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
        checkpoint = claimed_state.latest_sale_at
        fetched_records: list[SaleRecord] = []
        latest_page_records: list[SaleRecord] = []
        offset = 0

        try:
            for page_index in range(settings.history_max_pages_per_poll):
                await self._rate_limiter.acquire()
                payload = await client.get_auction_history(
                    item_id=item.id,
                    limit=settings.history_page_size,
                    offset=offset,
                )
                page_records, total = _parse_history_page(item.id, payload)
                if page_index == 0:
                    latest_page_records = page_records
                fetched_records.extend(page_records)

                if checkpoint is None or not page_records:
                    break
                oldest = min(record.sold_at for record in page_records)
                offset += len(page_records)
                if oldest <= checkpoint or offset >= total:
                    break

            new_records = _filter_new_records(
                fetched_records,
                checkpoint,
                set(claimed_state.latest_sale_fingerprints),
            )
            await self._save_history_success(
                item,
                new_records,
                fetched_records,
                latest_page_records,
            )
        except httpx.HTTPStatusError as exc:
            http_status = exc.response.status_code
            retry_after = _retry_after_seconds(exc.response)
            await self._save_history_error(item.id, http_status, retry_after)
            logger.warning("History request failed for %s: HTTP %s", item.id, http_status)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            await self._save_history_error(item.id, None, 60)
            logger.exception("History poll failed for %s: %s", item.id, exc)

    async def _poll_lots(
        self,
        client: StalzoneClient,
        item: MarketItem,
        claimed_state: LotPollState,
    ) -> None:
        records_by_fingerprint: dict[str, LotRecord] = {}
        offset = 0
        total_lots = 0
        complete_snapshot = False

        try:
            for _ in range(settings.lots_max_pages_per_poll):
                await self._rate_limiter.acquire()
                payload = await client.get_available_lots(
                    item_id=item.id,
                    limit=settings.history_page_size,
                    offset=offset,
                )
                page_records, total = _parse_lots_page(item.id, payload)
                total_lots = total
                records_by_fingerprint.update(
                    (record.fingerprint, record) for record in page_records
                )
                offset += len(page_records)
                if offset >= total:
                    complete_snapshot = True
                    break
                if not page_records:
                    break

            await self._save_lots_success(
                item,
                claimed_state,
                list(records_by_fingerprint.values()),
                total_lots,
                complete_snapshot,
            )
        except httpx.HTTPStatusError as exc:
            http_status = exc.response.status_code
            retry_after = _retry_after_seconds(exc.response)
            await self._save_lots_error(item.id, http_status, retry_after)
            logger.warning("Lots request failed for %s: HTTP %s", item.id, http_status)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            await self._save_lots_error(item.id, None, 60)
            logger.exception("Lots poll failed for %s: %s", item.id, exc)

    async def _save_history_success(
        self,
        item: MarketItem,
        records: list[SaleRecord],
        fetched_records: list[SaleRecord],
        latest_page_records: list[SaleRecord],
    ) -> None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            inserted = await store_sales(session, records)
            state = await session.get(HistoryPollState, item.id, with_for_update=True)
            db_item = await session.get(MarketItem, item.id, with_for_update=True)
            if state is None or db_item is None:
                return

            recent_sales = sum(
                1 for record in latest_page_records if record.sold_at >= now - timedelta(hours=1)
            )
            state.activity_score = state.activity_score * 0.7 + recent_sales * 0.3
            if db_item.configured_status == int(MarketStatus.AUTO):
                lot_state = await session.get(LotPollState, item.id)
                _update_auto_status(
                    db_item,
                    state,
                    lot_state.activity_score if lot_state is not None else 0.0,
                )

            if fetched_records:
                newest = max(record.sold_at for record in fetched_records)
                newest_fingerprints = {
                    record.fingerprint for record in fetched_records if record.sold_at == newest
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
            "Polled %s: received=%s inserted=%s next=%s",
            item.id,
            len(records),
            inserted,
            state.next_poll_at.isoformat(),
        )

    async def _save_lots_success(
        self,
        item: MarketItem,
        claimed_state: LotPollState,
        records: list[LotRecord],
        total_lots: int,
        complete_snapshot: bool,
    ) -> None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            new_count, disappeared_count = await store_lots(
                session,
                item.id,
                records,
                now,
                claimed_state.last_success_at,
                complete_snapshot,
            )
            state = await session.get(LotPollState, item.id, with_for_update=True)
            db_item = await session.get(MarketItem, item.id, with_for_update=True)
            history_state = await session.get(
                HistoryPollState,
                item.id,
                with_for_update=True,
            )
            if state is None or db_item is None or history_state is None:
                return

            changes = new_count + disappeared_count
            if claimed_state.last_success_at is None:
                changes_per_hour = 0.0
            else:
                elapsed_hours = max(
                    (now - claimed_state.last_success_at).total_seconds() / 3600,
                    1 / 60,
                )
                total_change = abs(total_lots - claimed_state.total_lots)
                changes_per_hour = (changes + total_change) / elapsed_hours
            state.activity_score = state.activity_score * 0.7 + changes_per_hour * 0.3
            if db_item.configured_status == int(MarketStatus.AUTO):
                _update_auto_status(db_item, history_state, state.activity_score)

            state.last_polled_at = now
            state.last_success_at = now
            state.last_http_status = 200
            state.consecutive_errors = 0
            state.total_lots = total_lots
            state.snapshot_complete = complete_snapshot
            state.next_poll_at = now + _poll_interval(db_item)

        logger.info(
            "Polled lots %s: active=%s new=%s disappeared=%s complete=%s next=%s",
            item.id,
            len(records),
            new_count,
            disappeared_count,
            complete_snapshot,
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

    async def _save_lots_error(
        self,
        item_id: str,
        http_status: int | None,
        retry_after_seconds: int,
    ) -> None:
        now = datetime.now(UTC)
        async with async_session_factory() as session, session.begin():
            state = await session.get(LotPollState, item_id, with_for_update=True)
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

    records: list[SaleRecord] = []
    for raw_price in raw_prices:
        if not isinstance(raw_price, dict):
            continue
        records.append(parse_sale(item_id, raw_price))

    raw_total = payload.get("total", len(records))
    total = int(raw_total) if isinstance(raw_total, (int, float, str)) else len(records)
    return records, max(total, len(records))


def _parse_lots_page(item_id: str, payload: Any) -> tuple[list[LotRecord], int]:
    if not isinstance(payload, dict):
        raise ValueError("lots response must be an object")
    raw_lots = payload.get("lots", [])
    if not isinstance(raw_lots, list):
        raise ValueError("lots response lots must be a list")

    records: list[LotRecord] = []
    for raw_lot in raw_lots:
        if not isinstance(raw_lot, dict):
            continue
        records.append(parse_lot(item_id, raw_lot))

    raw_total = payload.get("total", len(records))
    total = int(raw_total) if isinstance(raw_total, (int, float, str)) else len(records)
    return records, max(total, len(records))


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


def _update_auto_status(
    item: MarketItem,
    state: HistoryPollState,
    lot_activity_score: float = 0.0,
) -> None:
    if state.activity_score >= 20 or lot_activity_score >= 60:
        desired = MarketStatus.HOT
    elif state.activity_score >= 1 or lot_activity_score >= 3:
        desired = MarketStatus.NORMAL
    else:
        desired = MarketStatus.RARE

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
    configured = MarketStatus(item.configured_status)
    effective = MarketStatus(item.effective_status)
    if configured == MarketStatus.AUTO and effective == MarketStatus.RARE:
        return timedelta(seconds=settings.history_auto_bootstrap_interval_seconds)
    intervals = {
        MarketStatus.HOT: settings.history_hot_interval_seconds,
        MarketStatus.NORMAL: settings.history_normal_interval_seconds,
        MarketStatus.RARE: settings.history_rare_interval_seconds,
        MarketStatus.IGNORE: settings.history_auto_bootstrap_interval_seconds,
    }
    return timedelta(seconds=intervals[effective])


def _retry_after_seconds(response: httpx.Response) -> int:
    value = response.headers.get("Retry-After")
    if value is None:
        return 60
    try:
        return max(1, int(value))
    except ValueError:
        return 60


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await HistoryWorker().run()


if __name__ == "__main__":
    asyncio.run(main())
