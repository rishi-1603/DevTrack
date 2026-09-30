"""Tests for CORS origin resolution (finding S7 remediation).

Identical in intent to the sibling Repay-Master project's version of this file:
both repos had the same flaw, so both got the same fix and the same tests,
rather than two variations of it.

The interesting assertions here are the negative ones. The bug being prevented
is not "CORS is configured" but "wildcard origins and credentials are
configured *together*", which is a pairing that silently trusts every website.
So several tests below assert that a combination cannot be produced at all,
rather than that a particular value was returned.
"""
import pytest
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.cors import CorsConfigError, resolve_cors_origins
from app.main import app


class TestProductionRefusesWildcard:
    def test_wildcard_in_production_raises(self):
        with pytest.raises(CorsConfigError) as exc:
            resolve_cors_origins("*", "production")
        # The message has to be actionable: it names the setting and says what
        # to put in it instead, because this error fires at startup, possibly
        # in a container log nobody is watching live.
        assert "CORS_ORIGINS" in str(exc.value)
        assert "APP_ENV" in str(exc.value)

    @pytest.mark.parametrize("env", ["Production", "PRODUCTION", "production", " production "])
    def test_environment_match_is_case_and_space_insensitive(self, env):
        """`APP_ENV=Production` must not slip through a naive `== "production"`."""
        with pytest.raises(CorsConfigError):
            resolve_cors_origins("*", env)

    def test_wildcard_buried_in_a_list_still_raises(self):
        """A wildcard mixed with real origins is still a wildcard.

        Starlette treats `"*" in allow_origins` as allow-all regardless of what
        else is in the list, so `"https://app.example.com,*"` would look
        restrictive in a config file and behave permissively at runtime.
        """
        with pytest.raises(CorsConfigError):
            resolve_cors_origins("https://app.example.com,*", "production")

    def test_explicit_origins_in_production_are_returned_with_credentials(self):
        origins, allow_credentials = resolve_cors_origins(
            "https://app.example.com,https://admin.example.com", "production"
        )
        assert origins == ["https://app.example.com", "https://admin.example.com"]
        # Credentials are safe *because* the origins are explicit -- the browser
        # is only told to trust origins the operator named.
        assert allow_credentials is True


class TestWildcardNeverCarriesCredentials:
    def test_development_wildcard_is_allowed_but_credentials_off(self):
        origins, allow_credentials = resolve_cors_origins("*", "development")
        assert origins == ["*"]
        assert allow_credentials is False

    @pytest.mark.parametrize("env", ["development", "staging", "test", "", "something-new"])
    def test_no_non_production_environment_gets_credentials_with_a_wildcard(self, env):
        """The invariant, checked across every environment name but production."""
        origins, allow_credentials = resolve_cors_origins("*", env)
        assert not ("*" in origins and allow_credentials), (
            "wildcard origins with allow_credentials=True is the unsafe pairing"
        )


class TestParsingAndDefaults:
    def test_comma_list_is_stripped_and_empties_dropped(self):
        origins, _ = resolve_cors_origins(" http://a.test , ,http://b.test,", "development")
        assert origins == ["http://a.test", "http://b.test"]

    @pytest.mark.parametrize("raw", ["", "   ", ",,,", None])
    def test_unset_value_denies_cross_origin_instead_of_allowing_all(self, raw):
        """An empty setting must not be read as a wildcard.

        This is the direction of the fix that is easiest to get backwards:
        treating "operator set nothing" as "allow everything" is how the
        original default came to be a wildcard in the first place.
        """
        origins, allow_credentials = resolve_cors_origins(raw, "development")
        assert origins == []
        assert allow_credentials is False


class TestTheAppIsWiredConsistently:
    def _cors_kwargs(self):
        for middleware in app.user_middleware:
            if middleware.cls is CORSMiddleware:
                return middleware.kwargs
        raise AssertionError("CORSMiddleware is not installed on the app")

    def test_installed_middleware_matches_the_resolver(self):
        kwargs = self._cors_kwargs()
        expected_origins, expected_credentials = resolve_cors_origins(
            settings.CORS_ORIGINS, settings.APP_ENV
        )
        assert kwargs["allow_origins"] == expected_origins
        assert kwargs["allow_credentials"] is expected_credentials

    def test_no_wildcard_and_credentials_together_in_this_app(self):
        kwargs = self._cors_kwargs()
        assert not ("*" in kwargs["allow_origins"] and kwargs["allow_credentials"])


class TestObservedResponseHeaders:
    """Header-level proof, because the middleware kwargs could in principle
    differ from what Starlette actually emits."""

    def test_cross_origin_request_gets_no_credentials_header_under_wildcard(self):
        with TestClient(app) as client:
            response = client.get("/health", headers={"Origin": "https://evil.example.com"})
        assert response.status_code == 200
        # Under the development default (wildcard) Starlette answers with '*'
        # and must NOT add Access-Control-Allow-Credentials.
        assert "access-control-allow-credentials" not in {
            k.lower() for k in response.headers.keys()
        }
