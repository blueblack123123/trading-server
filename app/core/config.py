from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    stalzone_client_id: str = ""
    stalzone_client_secret: SecretStr = SecretStr("")
    stalzone_oauth_token_url: str = "https://exbo.net/oauth/token"
    stalzone_base_url: str = "https://eapi.stalzone.com"
    stalzone_region: str = "RU"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    admin_key: str = ""
    market_items_config_path: str = "config/market_items.json"
    exbo_database_path: str = "/app/external/stalzone-database"
    database_url: str = "postgresql+asyncpg://trading:trading@localhost:5432/trading"

    stalzone_requests_per_minute: int = 180
    collector_enabled: bool = False

    history_page_size: int = 200
    history_incremental_max_pages: int = 10
    history_live_requests_per_minute: int = 150
    history_backfill_requests_per_minute: int = 30
    history_backfill_min_records: int = 5_000
    history_backfill_fraction: float = 0.30
    history_backfill_max_records: int = 20_000
    history_backfill_page_overlap: int = 10
    history_max_hourly_points_per_item: int = 20_000
    history_raw_retention_hours: int = 48

    history_hot_interval_seconds: int = 60
    history_normal_interval_seconds: int = 3600
    history_rare_interval_seconds: int = 43200
    history_extremely_rare_interval_seconds: int = 604800
    history_worker_idle_seconds: float = 2.0
    lots_cache_ttl_seconds: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
