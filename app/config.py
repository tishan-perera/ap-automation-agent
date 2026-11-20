"""Application settings loaded from environment variables and an optional .env file."""

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    llm_provider: str = "mock"
    database_url: str = "sqlite:///./data/app.db"
    log_level: str = "INFO"
    amount_tolerance_usd: Decimal = Decimal("1.00")
    amount_tolerance_pct: Decimal = Decimal("0.01")

    # Optional API keys. Empty by default; never logged or stored in the database.
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
