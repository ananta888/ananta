"""OIDC-Token-Verifikation gegen Keycloak JWKS.

Cached JWKS für OIDC_JWKS_TTL Sekunden.
Gibt AuthContext zurück oder wirft ValueError.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import jwt
import requests

import config as cfg

log = logging.getLogger(__name__)

_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0


def _fetch_jwks() -> dict[str, Any]:
    global _jwks_cache, _jwks_fetched_at
    now = time.time()
    if _jwks_cache and (now - _jwks_fetched_at) < cfg.OIDC_JWKS_TTL:
        return _jwks_cache
    url = f"{cfg.OIDC_ISSUER.rstrip('/')}/protocol/openid-connect/certs"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = now
        log.debug("JWKS refreshed from %s", url)
    except Exception as exc:
        log.warning("JWKS fetch failed: %s", exc)
        if not _jwks_cache:
            raise ValueError(f"JWKS not available: {exc}") from exc
    return _jwks_cache


@dataclass(frozen=True)
class AuthContext:
    sub: str
    username: str
    issuer: str
    raw: dict[str, Any]


def verify_bearer_token(authorization_header: str) -> AuthContext:
    """Verifiziert einen Bearer-Token. Wirft ValueError bei ungültigem Token."""
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise ValueError("Missing or malformed Authorization header")
    raw_token = authorization_header[7:].strip()
    if not raw_token:
        raise ValueError("Empty token")

    jwks = _fetch_jwks()
    jwks_client = jwt.PyJWKClient.__new__(jwt.PyJWKClient)
    # Use PyJWT's JWKS key lookup
    signing_key = _get_signing_key(jwks, raw_token)

    options: dict[str, Any] = {"verify_exp": True, "verify_aud": bool(cfg.OIDC_AUDIENCE)}
    try:
        payload = jwt.decode(
            raw_token,
            signing_key,
            algorithms=["RS256", "ES256", "RS384", "RS512"],
            audience=cfg.OIDC_AUDIENCE or None,
            issuer=cfg.OIDC_ISSUER or None,
            options=options,
        )
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc

    sub = str(payload.get("sub") or "").strip()
    if not sub:
        raise ValueError("Token missing sub claim")

    username = (
        str(payload.get("preferred_username") or "")
        or str(payload.get("email") or "")
        or sub
    )
    return AuthContext(sub=sub, username=username, issuer=str(payload.get("iss") or ""), raw=payload)


def _get_signing_key(jwks: dict[str, Any], token: str) -> Any:
    """Holt den passenden Signing-Key aus JWKS anhand des token-Headers."""
    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        raise ValueError(f"Cannot decode token header: {exc}") from exc

    kid = header.get("kid")
    alg = header.get("alg", "RS256")
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
