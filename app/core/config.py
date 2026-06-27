from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    stalzone_internal_key: str = ""
    stalzone_base_url: str = "https://stalzone.wiki/donttouch/api"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    admin_key: str = ""
    market_items_config_path: str = "config/market_items.json"
    exbo_database_path: str = "/app/external/stalzone-database"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()