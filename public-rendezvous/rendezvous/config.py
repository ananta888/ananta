"""Konfiguration aus ENV-Variablen."""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
from urllib.parse import urlsplit


def _env(key: str, default: str = "") -> str:
    return str(os.environ.get(key) or default).strip()


def _required_secret(key: str) -> str:
    value = _env(key)
    if not value:
        raise RuntimeError(f"{key} must be configured")
    if len(value.encode("utf-8")) < 32:
        raise RuntimeError(f"{key} must contain at least 32 bytes")
    return value


def _positive_int_env(key: str, default: int) -> int:
    try:
        value = int(_env(key, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{key} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{key} must be a positive integer")
    return value


def _normalize_http_origin(value: str) -> str:
    if value == "null" or "*" in value:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must not contain null or wildcards")
    if "?" in value or "#" in value:
        raise RuntimeError("CORS_ALLOWED_ORIGINS entries must not contain a query or fragment")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("CORS_ALLOWED_ORIGINS contains an invalid origin") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise RuntimeError("CORS_ALLOWED_ORIGINS accepts only http and https origins")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise RuntimeError("CORS_ALLOWED_ORIGINS requires a host without user information")
    if parsed.path or parsed.query or parsed.fragment:
        raise RuntimeError("CORS_ALLOWED_ORIGINS entries must not contain a path, query, or fragment")

    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
        serialized_host = f"[{address.compressed}]" if address.version == 6 else str(address)
    except ValueError:
        try:
            serialized_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise RuntimeError("CORS_ALLOWED_ORIGINS contains an invalid host") from exc
        labels = serialized_host.split(".")
        if (
            len(serialized_host) > 253
            or any(not label or len(label) > 63 for label in labels)
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in labels)
        ):
            raise RuntimeError("CORS_ALLOWED_ORIGINS contains an invalid host")

    scheme = parsed.scheme.lower()
    default_port = 80 if scheme == "http" else 443
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{scheme}://{serialized_host}{port_suffix}"


def _cors_allowed_origins(value: str) -> frozenset[str]:
    entries = [entry.strip() for entry in value.split(",") if entry.strip()]
    if not entries:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must contain at least one exact origin")
    return frozenset(_normalize_http_origin(entry) for entry in entries)


OIDC_ISSUER = _env("OIDC_ISSUER", "https://keycloak.ananta.de/realms/ananta")
OIDC_ISSUERS_EXTRA = [i for i in _env("OIDC_ISSUERS_EXTRA", "").split(",") if i.strip()]
OIDC_AUDIENCE = _env("OIDC_AUDIENCE", "ananta-rendezvous")
OIDC_JWKS_TTL = _positive_int_env("OIDC_JWKS_TTL", 300)
OIDC_JWKS_MAX_AGE_SECONDS = _positive_int_env("OIDC_JWKS_MAX_AGE_SECONDS", 600)

TURN_SHARED_SECRET = _env("TURN_SHARED_SECRET", "")
RENDEZVOUS_SECURITY_SIGNING_SECRET = _required_secret("RENDEZVOUS_SECURITY_SIGNING_SECRET")
RENDEZVOUS_EXPECTED_SIGNING_KEY_ID = _env("RENDEZVOUS_EXPECTED_SIGNING_KEY_ID", "")
if RENDEZVOUS_EXPECTED_SIGNING_KEY_ID and not re.fullmatch(
    r"rv:[0-9a-f]{24}",
    RENDEZVOUS_EXPECTED_SIGNING_KEY_ID,
):
    raise RuntimeError("RENDEZVOUS_EXPECTED_SIGNING_KEY_ID must be an rv: key identifier")
if TURN_SHARED_SECRET and hmac.compare_digest(
    RENDEZVOUS_SECURITY_SIGNING_SECRET.encode("utf-8"),
    TURN_SHARED_SECRET.encode("utf-8"),
):
    raise RuntimeError("RENDEZVOUS_SECURITY_SIGNING_SECRET must be independent from TURN_SHARED_SECRET")
TURN_REALM = _env("TURN_REALM", "ananta.de")
TURN_URLS = [u.strip() for u in _env("TURN_URLS", "turn:webrtc.ananta.de:3478").split(",") if u.strip()]
TURN_TTL_SECONDS = _positive_int_env("TURN_TTL_SECONDS", 600)

RATE_JOIN_LIMIT = int(_env("RATE_JOIN_LIMIT", "10"))
RATE_JOIN_WINDOW = int(_env("RATE_JOIN_WINDOW", "60"))
RATE_CREATE_LIMIT = int(_env("RATE_CREATE_LIMIT", "5"))
RATE_CREATE_WINDOW = int(_env("RATE_CREATE_WINDOW", "60"))
RATE_SIGNAL_LIMIT = int(_env("RATE_SIGNAL_LIMIT", "30"))
RATE_SIGNAL_WINDOW = int(_env("RATE_SIGNAL_WINDOW", "10"))
RATE_SIGNAL_POLL_LIMIT = _positive_int_env("RATE_SIGNAL_POLL_LIMIT", 12)
RATE_SIGNAL_POLL_WINDOW = _positive_int_env("RATE_SIGNAL_POLL_WINDOW", 10)
RATE_TURN_CREDENTIAL_LIMIT = _positive_int_env("RATE_TURN_CREDENTIAL_LIMIT", 4)
RATE_TURN_CREDENTIAL_WINDOW = _positive_int_env("RATE_TURN_CREDENTIAL_WINDOW", 60)

SESSION_MAX_DURATION_SECONDS = int(_env("SESSION_MAX_DURATION_SECONDS", str(60 * 60)))  # 1h
SESSION_CLEANUP_INTERVAL_SECONDS = int(_env("SESSION_CLEANUP_INTERVAL_SECONDS", "300"))
RENDEZVOUS_DB_PATH = _env("RENDEZVOUS_DB_PATH", "/tmp/ananta-rendezvous.db")
RENDEZVOUS_DB_TIMEOUT_SECONDS = float(_env("RENDEZVOUS_DB_TIMEOUT_SECONDS", "5.0"))
CORS_ALLOWED_ORIGINS = _cors_allowed_origins(
    _env(
        "CORS_ALLOWED_ORIGINS",
        ("http://127.0.0.1:4200,http://localhost:4200,https://127.0.0.1,https://localhost"),
    )
)

LOG_LEVEL = _env("LOG_LEVEL", "INFO")
