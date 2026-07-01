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
    live_requests_per_minute: int
    backfill_min_requests_per_minute: int
    backfill_max_requests_per_minute: int
    backfill_live_backlog_threshold: int
    raw_retention_hours: int
    hourly_retention_hours: int
    max_raw_points_per_item: int
    max_aggregate_points_per_item: int
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


class HistoryStatusResponse(BaseModel):
    generated_at: datetime
    settings: HistoryCollectionSettings
    backfill: HistoryBackfillStatus
    storage: HistoryStorageStatus
