"""OIDC token validation against a configured JWKS endpoint.

Used only by explicit account-link and token-exchange endpoints. Normal Hub
routes continue to validate Hub-issued JWTs. The external token's signature
is verified against the provider's JWKS endpoint, and the standard claims
(`iss`, `aud`, `exp`, `nbf`, `iat`) are checked.

Default-deny: if any required claim is missing or invalid, validation
returns None. The caller MUST treat None as "unauthenticated" — never
silently fall back to the secret-key path.

JWKS caching: `PyJWKClient` keeps the keyset in memory for the
configured TTL (default 1h, see `OIDC_JWKS_CACHE_SECONDS`). Keycloak
rotates signing keys every 24h by default, so 1h cache is safe.

Single responsibility: validate one token, return its claims dict or
None. No Flask, no request context, no side effects.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

import jwt

from agent.services.oidc_settings import OidcConfig, get_oidc_config

logger = logging.getLogger(__name__)


class _JwksCacheEntry:
    __slots__ = ("client", "fetched_at", "url")

    def __init__(self, client: jwt.PyJWKClient, fetched_at: float, url: str) -> None:
        self.client = client
        self.fetched_at = fetched_at
        self.url = url


_jwks_cache: dict[str, _JwksCacheEntry] = {}
_jwks_lock = threading.Lock()


def _get_jwks_client(cfg: OidcConfig) -> jwt.PyJWKClient:
    """Return a cached PyJWKClient for cfg.jwks_url, refreshing on TTL expiry."""
    now = time.monotonic()
    with _jwks_lock:
        entry = _jwks_cache.get(cfg.jwks_url)
        if entry is not None and (now - entry.fetched_at) < cfg.jwks_cache_seconds:
            return entry.client
        # PyJWKClient fetches keys lazily on first .get_signing_key_from_jwt() call
        client = jwt.PyJWKClient(cfg.jwks_url, cache_keys=True, lifespan=cfg.jwks_cache_seconds)
        _jwks_cache[cfg.jwks_url] = _JwksCacheEntry(client=client, fetched_at=now, url=cfg.jwks_url)
        return client


def _clear_jwks_cache() -> None:
    """Test helper: drop all cached JWKS clients."""
    with _jwks_lock:
        _jwks_cache.clear()


def validate_oidc_token(token: str, cfg: Optional[OidcConfig] = None) -> Optional[dict[str, Any]]:
    """Validate an OIDC access token against cfg.

    Returns the claims dict on success, None on any failure.
    Never raises — all exceptions are caught and logged.
    """
    if cfg is None:
        cfg = get_oidc_config()
    if not cfg.enabled:
        return None
    if not cfg.jwks_url or not cfg.audience or not cfg.issuer_url:
        return None

    try:
        signing_key = _get_jwks_client(cfg).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(cfg.allowed_algorithms),
            audience=cfg.audience,
            issuer=cfg.issuer_url,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub"],
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
                "verify_signature": True,
            },
        )
        # OIDC convention: the user identifier is `sub`. We mirror the shape
        # that `check_user_auth` already expects so the rest of the system
        # does not need to know whether the token came from OIDC or the
        # Hub's own secret_key JWT.
        return dict(claims)
    except jwt.ExpiredSignatureError:
        logger.info("OIDC token validation failed: token expired")
        return None
    except jwt.InvalidAudienceError:
        logger.warning("OIDC token validation failed: invalid audience")
        return None
    except jwt.InvalidIssuerError:
        logger.warning("OIDC token validation failed: invalid issuer")
        return None
    except jwt.InvalidTokenError as exc:
        logger.info("OIDC token validation failed: %s", exc)
        return None
    except Exception as exc:  # network errors, malformed JWKS, etc.
        logger.warning("OIDC token validation error (non-fatal): %s", exc)
        return None
