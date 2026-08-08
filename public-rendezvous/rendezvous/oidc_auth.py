"""OIDC-Token-Verifikation gegen Keycloak JWKS.

Cached JWKS für OIDC_JWKS_TTL Sekunden. Bei einem Refresh-Fehler darf
der Cache höchstens OIDC_JWKS_MAX_AGE_SECONDS alt sein.
Gibt AuthContext zurück oder wirft ValueError.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

import jwt
import requests

import config as cfg

log = logging.getLogger(__name__)

_jwks_cache: dict[str, dict[str, Any]] = {}  # issuer → jwks
_jwks_fetched_at: dict[str, float] = {}  # issuer → timestamp


def _jwks_cache_age(issuer: str, *, now: float) -> float:
    fetched_at = _jwks_fetched_at.get(issuer)
    if fetched_at is None:
        return float("inf")
    return max(0.0, now - fetched_at)


def _fetch_jwks(issuer: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _jwks_cache.get(issuer)
    normal_cache_age = min(cfg.OIDC_JWKS_TTL, cfg.OIDC_JWKS_MAX_AGE_SECONDS)
    if cached and _jwks_cache_age(issuer, now=now) < normal_cache_age:
        return cached
    url = f"{issuer.rstrip('/')}/protocol/openid-connect/certs"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        _jwks_cache[issuer] = data
        _jwks_fetched_at[issuer] = time.monotonic()
        log.debug("JWKS refreshed from %s", url)
        return data
    except Exception as exc:
        log.warning("JWKS fetch failed for %s: %s", issuer, exc)
        cache_age = _jwks_cache_age(issuer, now=time.monotonic())
        if cached and cache_age <= cfg.OIDC_JWKS_MAX_AGE_SECONDS:
            log.warning("Using stale JWKS for %s (age %.1fs)", issuer, cache_age)
            return cached
        raise ValueError(f"JWKS not available for {issuer}: {exc}") from exc


def _trusted_issuers() -> list[str]:
    issuers = [cfg.OIDC_ISSUER]
    for extra in cfg.OIDC_ISSUERS_EXTRA:
        extra = extra.strip()
        if extra and extra not in issuers:
            issuers.append(extra)
    return issuers


@dataclass(frozen=True)
class AuthContext:
    sub: str
    username: str
    issuer: str
    raw: dict[str, Any]

    @property
    def peer_id(self) -> str:
        """Return the stable, non-display identity used for authorization.

        Usernames and email addresses are mutable Keycloak display claims.  A
        public rendezvous membership is therefore bound to the verified OIDC
        issuer and subject pair instead.
        """
        return canonical_peer_id(self.issuer, self.sub)


def canonical_peer_id(issuer: str, subject: str) -> str:
    """Derive an opaque peer identifier from a verified ``(iss, sub)`` pair."""
    normalized_issuer = str(issuer or "").strip().rstrip("/")
    normalized_subject = str(subject or "").strip()
    if not normalized_issuer or not normalized_subject:
        raise ValueError("OIDC issuer and subject are required")
    material = (
        b"ananta.public-rendezvous.peer-id.v1\0"
        + normalized_issuer.encode("utf-8")
        + b"\0"
        + normalized_subject.encode("utf-8")
    )
    return "oidc:" + hashlib.sha256(material).hexdigest()


def verify_bearer_token(authorization_header: str) -> AuthContext:
    """Verifiziert einen Bearer-Token gegen alle konfigurierten Issuer. Wirft ValueError wenn keiner passt."""
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise ValueError("Missing or malformed Authorization header")
    raw_token = authorization_header[7:].strip()
    if not raw_token:
        raise ValueError("Empty token")

    options: dict[str, Any] = {
        "verify_exp": True,
        "verify_aud": bool(cfg.OIDC_AUDIENCE),
        "require": ["exp", "iss", "sub", "aud"],
    }
    last_error: Exception | None = None
    for issuer in _trusted_issuers():
        try:
            jwks = _fetch_jwks(issuer)
        except ValueError:
            continue
        try:
            signing_key = _get_signing_key(jwks, raw_token)
            payload = jwt.decode(
                raw_token,
                signing_key,
                algorithms=["RS256", "ES256", "RS384", "RS512"],
                audience=cfg.OIDC_AUDIENCE or None,
                issuer=issuer or None,
                options=options,
            )
        except jwt.ExpiredSignatureError as exc:
            raise ValueError("Token expired") from exc
        except (jwt.InvalidTokenError, ValueError) as exc:
            last_error = exc
            continue

        sub = str(payload.get("sub") or "").strip()
        if not sub:
            raise ValueError("Token missing sub claim")
        username = str(payload.get("preferred_username") or "") or str(payload.get("email") or "") or sub
        token_issuer = str(payload.get("iss") or "").strip()
        if not token_issuer:
            raise ValueError("Token missing iss claim")
        return AuthContext(sub=sub, username=username, issuer=token_issuer, raw=payload)

    raise ValueError(f"Invalid token: {last_error}")


def _get_signing_key(jwks: dict[str, Any], token: str) -> Any:
    """Holt den passenden Signing-Key aus JWKS anhand des token-Headers."""
    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise ValueError(f"Cannot decode token header: {exc}") from exc

    kid = header.get("kid")
    keys = jwks.get("keys") or []

    for key_data in keys:
        if kid and key_data.get("kid") != kid:
            continue
        try:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
        except Exception:
            try:
                return jwt.algorithms.ECAlgorithm.from_jwk(key_data)
            except Exception:
                continue

    raise ValueError(f"No matching key found in JWKS for kid={kid}")
