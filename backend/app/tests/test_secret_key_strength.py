"""Tests for the SECRET_KEY strength check (finding S15).

`app/core/config.py` already refused to start with a *missing* SECRET_KEY (a
Day-3 fix). This pins the other half: a *weak* one is refused in production and
warned about loudly elsewhere, which is the failure mode that survives a
deployment checklist, because everything appears to work.

Why 32 bytes: HS256 uses the secret directly as an HMAC key, and RFC 7518
section 3.2 requires the key to be at least as long as the hash output. PyJWT
emits an `InsecureKeyLengthWarning` below that; a warning in a container log is
not a control, so in production the check is a startup failure instead.

Why not a failure everywhere: this project is deployed (Render, auto-deploying
from `main`), and a rule that fails unconditionally could take a live service
down over a development key. The asymmetry matches `app/core/cors.py`, which is
strict about wildcard origins in production only.
"""
import secrets

import pytest
from pydantic import ValidationError

from app.core.config import MIN_SECRET_KEY_BYTES, Settings


def _settings(secret: str, monkeypatch, app_env: str = "development") -> Settings:
    """Build Settings from explicit values.

    Environment variables outrank constructor kwargs for pydantic-settings, and
    `conftest.py` exports a SECRET_KEY for the rest of the suite, so the value
    under test has to be injected through the environment -- otherwise every
    case here would silently test conftest's key instead.
    """
    monkeypatch.setenv("SECRET_KEY", secret)
    monkeypatch.setenv("APP_ENV", app_env)
    return Settings(_env_file=None)


class TestProductionRefusesWeakKeys:
    @pytest.mark.parametrize("secret", ["", "x", "devtrack", "change-me", "a" * (MIN_SECRET_KEY_BYTES - 1)])
    def test_short_secret_refuses_to_start_in_production(self, secret, monkeypatch):
        with pytest.raises(ValidationError) as exc:
            _settings(secret, monkeypatch, app_env="production")
        message = str(exc.value)
        # Actionable, because it fires at container startup, possibly in a log
        # nobody is watching live.
        assert "SECRET_KEY" in message
        assert str(MIN_SECRET_KEY_BYTES) in message
        assert "secrets.token_hex" in message

    @pytest.mark.parametrize("env", ["Production", "PRODUCTION", " production "])
    def test_production_match_is_case_and_space_insensitive(self, env, monkeypatch):
        with pytest.raises(ValidationError):
            _settings("short", monkeypatch, app_env=env)

    def test_a_generated_key_starts_cleanly_in_production(self, monkeypatch):
        """The command the error message tells you to run must actually work."""
        settings = _settings(secrets.token_hex(32), monkeypatch, app_env="production")
        assert settings.APP_ENV == "production"


class TestNonProductionWarnsInsteadOfFailing:
    def test_short_secret_warns_but_still_starts_outside_production(self, monkeypatch):
        with pytest.warns(UserWarning, match="not acceptable in production"):
            settings = _settings("dev-only-short-key", monkeypatch, app_env="development")
        assert settings.SECRET_KEY == "dev-only-short-key"

    def test_no_warning_when_the_key_is_strong_enough(self, monkeypatch, recwarn):
        _settings(secrets.token_hex(32), monkeypatch, app_env="development")
        assert not [w for w in recwarn if "SECRET_KEY" in str(w.message)]


class TestTheThresholdItself:
    def test_threshold_is_the_rfc_minimum_not_an_arbitrary_number(self):
        """32 bytes is the SHA-256 output length RFC 7518 3.2 requires for an
        HS256 key. Pinning it means lowering it is a deliberate act that breaks
        a test, not a typo."""
        assert MIN_SECRET_KEY_BYTES == 32

    @pytest.mark.parametrize("length", [32, 33, 64, 128])
    def test_keys_at_or_above_the_minimum_are_accepted(self, length, monkeypatch):
        secret = "a" * length
        assert _settings(secret, monkeypatch, app_env="production").SECRET_KEY == secret
