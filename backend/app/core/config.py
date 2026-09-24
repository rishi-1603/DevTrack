"""Application configuration loaded from environment variables."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings, backed by environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "DevTrack"
    APP_ENV: str = "development"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "postgresql://devtrack:devtrack@localhost:5432/devtrack"

    # JWT
    # No default on purpose (Day 3 fix): every previous version of this
    # file shipped a hardcoded fallback ("change-this-secret-key-in-
    # production") that would have silently signed real JWTs in
    # production if an operator forgot to set SECRET_KEY -- the app would
    # start up fine and look like it worked. Requiring the field with no
    # default makes a missing SECRET_KEY a loud startup failure instead of
    # a silent security hole. See backend/app/tests/conftest.py for how
    # tests supply one, and .env.example / docker-compose.yml for how a
    # real deployment must.
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None
    DASHBOARD_CACHE_TTL_SECONDS: int = 60

    # Logging
    LOG_DIR: str = "logs"
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ORIGINS: str = "*"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
