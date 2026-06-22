from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # service
    ENVIRONMENT: str = "local"
    DEBUG: bool = True
    SECRET_KEY: str = "dev-insecure-000000000000000000000000000000"

    # database (same Postgres as portfolio-base)
    DATABASE_URL: str = "postgresql+asyncpg://raybags:raybags@localhost:5432/raybags"

    # redis (for pub/sub session routing)
    REDIS_URL: str = "redis://localhost:6379/1"

    # LLM
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # portfolio API (to issue tokens, etc.)
    PORTFOLIO_API_URL: str = "https://raybags.com/api/v1"
    PORTFOLIO_ADMIN_TOKEN: str | None = None  # service-to-service admin JWT

    # notifications
    DISCORD_WEBHOOK: str | None = None

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,https://raybags.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
