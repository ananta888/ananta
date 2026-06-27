"""OIDC settings access layer.

Reads the OIDC-related fields from `agent.config.settings` and exposes
them as a typed object. The OIDC bridge is **opt-in**: `OIDC_ENABLED`
defaults to False. When False, the Hub keeps using its own secret_key
JWT for user auth — exactly the existing behaviour.

When `OIDC_ENABLED=True`, the Hub validates user tokens against the
configured JWKS endpoint instead of its own secret. See
`docs/identity-architecture.md` for the full opt-in recipe.

Single responsibility:
- Read the relevant config fields
- Validate the config is internally consistent (if enabled, the
  required URLs/audience must be present)
- Expose the data to the auth layer without leaking pydantic internals

Default-deny: if `oidc_enabled` is True but a required field is empty,
`oidc_is_configured()` returns False so the Hub refuses to engage the
OIDC path and keeps using the secret-key path. This prevents silent
fallbacks to an insecure config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agent.config import settings


@dataclass(frozen=True)
class OidcConfig:
    enabled: bool
    issuer_url: str
    jwks_url: str
    audience: str
    client_id: str
    jwks_cache_seconds: int
    allowed_algorithms: tuple[str, ...]


def _load() -> OidcConfig:
    algos = tuple(
        a.strip() for a in (settings.oidc_allowed_algorithms or "RS256").split(",") if a.strip()
    ) or ("RS256",)
    return OidcConfig(
        enabled=bool(settings.oidc_enabled),
        issuer_url=str(settings.oidc_issuer_url or "").strip(),
        jwks_url=str(settings.oidc_jwks_url or "").strip(),
        audience=str(settings.oidc_audience or "").strip(),
        client_id=str(settings.oidc_client_id or "").strip(),
        jwks_cache_seconds=int(settings.oidc_jwks_cache_seconds or 3600),
        allowed_algorithms=algos,
    )


def get_oidc_config() -> OidcConfig:
    """Return the current OIDC config (cheap, re-reads settings each call)."""
    return _load()


def oidc_is_configured() -> bool:
    """True iff OIDC is enabled AND all required fields are present.

    Used by `check_user_auth` to decide whether to engage the JWKS path.
    Default-deny: a partial config does not silently fall back to the
    secret-key path — it returns False so the caller can decide what to
    do (e.g. return 503 or refuse to authenticate).
    """
    cfg = _load()
    if not cfg.enabled:
        return False
    required = (cfg.issuer_url, cfg.jwks_url, cfg.audience, cfg.client_id)
    return all(required)


def reset_oidc_cache() -> None:
    """No-op placeholder for the JWKS cache layer (Welle 3 will populate)."""
    return None