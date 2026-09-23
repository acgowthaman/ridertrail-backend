from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ors_api_key: Optional[str] = None
    owm_api_key: Optional[str] = None
    cors_origins: str = "http://localhost:3000"
    mongodb_uri: str = "mongodb://127.0.0.1:27017/"
    mongodb_database: str = "RiderTrail"
    mongodb_server_selection_timeout_ms: int = 5000

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
