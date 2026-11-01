"""Runtime configuration, read from the environment and a git-ignored `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BOT_CONTACT_PLACEHOLDER = "[YOUR EMAIL]"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    api_url: str = Field("http://localhost:8081", validation_alias="KOMBIN_API_URL")
    internal_key: SecretStr = Field(SecretStr(""), validation_alias="KOMBIN_INTERNAL_KEY")
    bot_contact: str = Field(
        BOT_CONTACT_PLACEHOLDER, validation_alias=AliasChoices("BOT_CONTACT", "KOMBIN_BOT_CONTACT")
    )

    requests_per_second: float = Field(1.0, gt=0,validation_alias="KOMBIN_REQUESTS_PER_SECOND")
    concurrency: int = Field(4, ge=1, le=4, validation_alias="KOMBIN_CONCURRENCY")
    max_pages: int = Field(5, ge=1, validation_alias="KOMBIN_MAX_PAGES")
    batch_size: int = Field(200, ge=1, le=500, validation_alias="KOMBIN_BATCH_SIZE")
    max_consecutive_failures: int = Field(20, ge=1, validation_alias="KOMBIN_MAX_CONSECUTIVE_FAILURES")
    http_timeout_seconds: float = Field(30.0, gt=0, validation_alias="KOMBIN_HTTP_TIMEOUT")

    log_json: bool = Field(False, validation_alias="KOMBIN_LOG_JSON")
    run_interval_hours: float = Field(6.0, gt=0, validation_alias="KOMBIN_RUN_INTERVAL_HOURS")
    sweep_interval_hours: float = Field(24.0, gt=0, validation_alias="KOMBIN_SWEEP_INTERVAL_HOURS")
    jitter_seconds: float = Field(600.0, ge=0, validation_alias="KOMBIN_JITTER_SECONDS")

    @property
    def user_agent(self) -> str:
        return f"KombinBot/0.1 (+https://kombin.app/bot; contact: {self.bot_contact})"


@lru_cache
def get_settings() -> Settings:
    return Settings()
