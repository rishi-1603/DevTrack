"""Application configuration loaded from environment variables."""
import warnings
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 signs with the secret as a raw HMAC key. RFC 7518 section 3.2 requires
# the key to be at least as long as the hash output -- 32 bytes for SHA-256 --
# and PyJWT emits an InsecureKeyLengthWarning below that. A short key is not a
# style problem: it makes the signature brute-forceable offline from any single
# captured token. Enforced as a hard startup failure rather than a warning,
# because a warning in a container log is not a control (finding S15).
MIN_SECRET_KEY_BYTES = 32


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

    @model_validator(mode="after")
    def _secret_key_must_be_long_enough(self) -> "Settings":
        """Refuse to run in production with a signing key short enough to
        brute-force (finding S15).

        Requiring the field (above) stops a *missing* secret; this stops a
        *weak* one, which is the failure mode that survives a checklist --
        someone sets SECRET_KEY=devtrack and everything appears to work.

        It is a `model_validator` rather than a `field_validator` because the
        decision depends on two fields: below the minimum it is a hard failure
        when `APP_ENV=production` and a loud warning otherwise. That asymmetry
        is deliberate and matches `app/core/cors.py`. This project is deployed
        (Render, auto-deploying from `main`), so a rule that fails unconditionally
        could take a live service down over a development-environment key --
        which would trade a real control for an outage. Production is where the
        key protects real tokens, so production is where it is enforced.
        """
        length = len(self.SECRET_KEY.encode("utf-8"))
        if length >= MIN_SECRET_KEY_BYTES:
            return self

        message = (
            f"SECRET_KEY is {length} bytes; at least {MIN_SECRET_KEY_BYTES} are required, "
            "because HS256 uses it directly as an HMAC key (RFC 7518 3.2) and a shorter key "
            "can be brute-forced offline from any single captured token. Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
        if self.APP_ENV.strip().lower() == "production":
            raise ValueError(f"Refusing to start in production: {message}")
        warnings.warn(f"Non-production only, and not acceptable in production: {message}", stacklevel=2)
        return self
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

    # WebSocket handshake ticket lifetime (Day-7, finding S9). Short on
    # purpose: this credential travels in a query string, which gets logged, so
    # its value is bounded by how long a leaked one stays usable. A client mints
    # one per connection; 60 seconds is far more than a handshake needs.
    WS_TOKEN_EXPIRE_SECONDS: int = 60

    # CORS
    #
    # The wildcard default survives for development convenience ONLY, and it can
    # no longer do damage: app/core/cors.py refuses a wildcard when
    # APP_ENV=production (loud startup failure) and never pairs a wildcard with
    # allow_credentials in any environment. A production deployment must set
    # this to the exact origins it serves. See finding S7 in the audit.
    CORS_ORIGINS: str = "*"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()
