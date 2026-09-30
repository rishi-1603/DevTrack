"""CORS origin resolution.

Added in the Day-7 security remediation (finding S7 in the audit's security
register). The same module exists in the sibling Repay-Master project; both had
the identical flaw, so both got the identical fix rather than two variations.
The problem is specific and worth stating precisely, because the naive
description of it overstates today's risk:

Before this module, `main.py` did::

    origins = ["*"] if settings.CORS_ORIGINS == "*" else settings.CORS_ORIGINS.split(",")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, ...)

and `CORS_ORIGINS` defaulted to `"*"`. Wildcard origins combined with
`allow_credentials=True` is the pairing that matters: in that combination
Starlette does NOT send `Access-Control-Allow-Origin: *` (browsers forbid that
with credentials). It echoes the request's `Origin` back and adds
`Access-Control-Allow-Credentials: true`, which means *any* website is trusted
to make credentialed cross-origin requests and read the responses.

How much of that was actually exploitable here: not much, today. This API
authenticates with `Authorization: Bearer` tokens (see
`app/core/dependencies.py`) and never calls `set_cookie`, so a malicious page
has no ambient credential to ride -- it cannot make the browser attach a token
it cannot read. The finding is therefore MEDIUM and not HIGH: a latent
misconfiguration that becomes a real cross-origin data-theft bug the moment
anyone adds a cookie or a session. That is exactly the kind of thing worth
fixing before it is load-bearing rather than after.

Two rules follow, and both are enforced here rather than left to an operator
remembering them:

1. A wildcard origin is refused outright when `APP_ENV` is production. Startup
   fails loudly, which is the pattern this codebase already uses for
   `SECRET_KEY` (see `core/config.py`) -- a missing or wrong value should stop
   the process, not quietly weaken it.
2. `allow_credentials` is derived, never hardcoded: it is `True` only when the
   operator listed explicit origins. There is no configuration in which a
   wildcard and credentials coexist, in any environment.
"""

WILDCARD = "*"


class CorsConfigError(RuntimeError):
    """Raised when the CORS configuration is unsafe for the target environment.

    A distinct type (rather than a bare RuntimeError) so the failure is
    greppable and so a caller can catch it without swallowing unrelated
    errors.
    """


def resolve_cors_origins(raw: str, app_env: str) -> tuple[list[str], bool]:
    """Turn the `CORS_ORIGINS` / `APP_ENV` settings into middleware arguments.

    Returns `(allow_origins, allow_credentials)`.

    An empty or whitespace-only value resolves to `([], False)` -- no
    cross-origin access at all -- rather than to a wildcard. Denying by default
    is the safe reading of an unset value; a wildcard is something an operator
    has to ask for explicitly.
    """
    if raw is None:
        raw = ""
    requested = [o.strip() for o in raw.split(",") if o.strip()]
    is_production = (app_env or "").strip().lower() == "production"

    if not requested:
        # Unset, empty, whitespace-only or commas-only: deny cross-origin
        # access entirely. Starlette would ignore the credentials flag with no
        # allowed origins, so returning True here would be harmless today -- but
        # the documented contract of this function is that an unset value never
        # comes back permissive, and a future `allow_origin_regex` or a caller
        # that inspects the flag directly would make "harmless" wrong. Found by
        # test_unset_value_denies_cross_origin_instead_of_allowing_all, which
        # failed against the first implementation of this module.
        return [], False

    if WILDCARD in requested:
        if is_production:
            raise CorsConfigError(
                "CORS_ORIGINS='*' is not permitted when APP_ENV=production. "
                "With credentials enabled, Starlette echoes the request Origin "
                "instead of sending '*', so a wildcard would trust every "
                "website to make credentialed cross-origin calls. Set "
                "CORS_ORIGINS to the exact comma-separated list of front-end "
                "origins this deployment serves (e.g. "
                "'https://app.example.com')."
            )
        # Wildcard is acceptable for local development, but never alongside
        # credentials -- see rule 2 in the module docstring.
        return [WILDCARD], False

    # Explicit origins: credentials are safe because the browser is only ever
    # told to trust origins the operator named.
    return requested, True
