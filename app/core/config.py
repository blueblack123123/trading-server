from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    stalzone_internal_key: str = ""
    stalzone_base_url: str = "https://stalzone.wiki/donttouch/api"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    admin_key: str = ""
    market_items_config_path: str = "config/market_items.json"
    exbo_database_path: str = "/app/external/stalzone-database"
    database_url: str = "postgresql+asyncpg://trading:trading@localhost:5432/trading"

    stalzone_requests_per_minute: int = 5
    collector_enabled: bool = False

    history_hot_interval_seconds: int = 60
    history_normal_interval_seconds: int = 3600
    history_rare_interval_seconds: int = 43200
    history_auto_bootstrap_interval_seconds: int = 86400
    history_worker_idle_seconds: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
