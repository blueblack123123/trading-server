from datetime import datetime
from enum import IntEnum

from pydantic import BaseModel


class MarketStatus(IntEnum):
    AUTO = 0
    HOT = 1
    NORMAL = 2
    RARE = 3
    IGNORE = 4
    EXTREMELY_RARE = 5


class MarketItemConfig(BaseModel):
    id: str
    name: str
    status: MarketStatus = MarketStatus.AUTO


class HistoryCollectionSettings(BaseModel):
    stalzone_requests_per_minute: int
    live_requests_per_minute: int
    backfill_min_requests_per_minute: int
    backfill_max_requests_per_minute: int
    backfill_live_backlog_threshold: int
    lots_collection_enabled: bool
    lots_requests_per_minute: int
    lots_poll_interval_seconds: int
    lots_page_size: int
    raw_retention_hours: int
    hourly_retention_hours: int
    max_raw_points_per_item: int
    max_aggregate_points_per_item: int
    max_module_aggregate_points_per_item: int
    compaction_interval_seconds: int


class HistoryBackfillStatus(BaseModel):
    tracked_items: int
    complete_items: int
    pending_items: int
    due_live_items: int
    selected_backfill_requests_per_minute: int
    oldest_backfill_next_at: datetime | None
    newest_backfill_success_at: datetime | None
    total_backfill_offset: int
    total_backfill_target: int


class HistoryResolutionStorage(BaseModel):
    resolution: str
    points: int
    items: int


class HistoryTopItemStorage(BaseModel):
    item_id: str
    name: str
    backfill_complete: bool
    backfill_offset: int
    backfill_target: int
    raw_points: int
    aggregate_points: int
    aggregate_keys: int
    total_points: int


class HistoryStorageStatus(BaseModel):
    raw_points: int
    aggregate_points: int
    active_lots: int
    inactive_lots: int
    database_size_bytes: int | None
    aggregate_resolutions: list[HistoryResolutionStorage]
    top_items: list[HistoryTopItemStorage]
    items: list[HistoryTopItemStorage]


class RequestBudgetStatus(BaseModel):
    stalzone_limit_per_minute: int
    live_history_per_minute: int
    backfill_per_minute: int
    active_lots_per_minute: int
    configured_workers_per_minute: int
    spare_per_minute: int
    utilization_percent: float
    capped_by_global_limiter: bool


class HistoryStatusResponse(BaseModel):
    generated_at: datetime
    settings: HistoryCollectionSettings
    backfill: HistoryBackfillStatus
    storage: HistoryStorageStatus
    request_budget: RequestBudgetStatus
