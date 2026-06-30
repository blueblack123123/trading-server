from fastapi import APIRouter

from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/capabilities")
async def capabilities() -> dict[str, object]:
    return {
        "history_resolutions": ["auto", "raw", "20min", "hour", "day"],
        "history_filters": ["qlt", "definition_id", "additional_key"],
        "history_filter_semantics": {
            "definition_id": "all required; _pre/_suf/_aff variants are matched",
            "additional_key": "exact aggregate additional payload key",
        },
        "aggregate_additional": True,
        "raw_additional": True,
        "retention": {
            "raw_hours": settings.history_raw_retention_hours,
            "hourly_hours": settings.history_hourly_retention_hours,
            "max_raw_points_per_item": settings.history_max_raw_points_per_item,
            "max_aggregate_points_per_item": settings.history_max_aggregate_points_per_item,
        },
    }
