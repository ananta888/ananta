"""Konfiguration aus ENV-Variablen."""
from __future__ import annotations

import os


def _env(key: str, default: str = "") -> str:
    return str(os.environ.get(key) or default).strip()


OIDC_ISSUER = _env("OIDC_ISSUER", "https://keycloak.ananta.de/realms/ananta")
OIDC_AUDIENCE = _env("OIDC_AUDIENCE", "ananta-hub")
OIDC_JWKS_TTL = int(_env("OIDC_JWKS_TTL", "300"))  # Sekunden

TURN_SHARED_SECRET = _env("TURN_SHARED_SECRET", "")
TURN_REALM = _env("TURN_REALM", "ananta.de")
TURN_URLS = [u.strip() for u in _env("TURN_URLS", "turn:webrtc.ananta.de:3478").split(",") if u.strip()]
TURN_TTL_SECONDS = int(_env("TURN_TTL_SECONDS", "3600"))

RATE_JOIN_LIMIT = int(_env("RATE_JOIN_LIMIT", "10"))
RATE_JOIN_WINDOW = int(_env("RATE_JOIN_WINDOW", "60"))
RATE_CREATE_LIMIT = int(_env("RATE_CREATE_LIMIT", "5"))
RATE_CREATE_WINDOW = int(_env("RATE_CREATE_WINDOW", "60"))
RATE_SIGNAL_LIMIT = int(_env("RATE_SIGNAL_LIMIT", "30"))
RATE_SIGNAL_WINDOW = int(_env("RATE_SIGNAL_WINDOW", "10"))

SESSION_MAX_DURATION_SECONDS = int(_env("SESSION_MAX_DURATION_SECONDS", str(60 * 60)))  # 1h
SESSION_CLEANUP_INTERVAL_SECONDS = int(_env("SESSION_CLEANUP_INTERVAL_SECONDS", "300"))
RENDEZVOUS_DB_PATH = _env("RENDEZVOUS_DB_PATH", "/tmp/ananta-rendezvous.db")
RENDEZVOUS_DB_TIMEOUT_SECONDS = float(_env("RENDEZVOUS_DB_TIMEOUT_SECONDS", "5.0"))

LOG_LEVEL = _env("LOG_LEVEL", "INFO")
