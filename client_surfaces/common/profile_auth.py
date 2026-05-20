from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from client_surfaces.common.types import ClientProfile

_SECRET_KEY_PARTS = ("token", "secret", "password", "private_key", "credential", "api_key", "api-key")
_SECRET_INLINE = re.compile(r"(?i)(token|secret|password|private[_-]?key|credential|api[_-]?key)[=:]\S+")


def _clean_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    return text[: max(1, int(max_chars))]


def _normalize_base_url(value: Any) -> str:
    base = _clean_text(value or "http://localhost:8080", max_chars=240).rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https://")
    return base


def build_client_profile(raw: dict[str, Any]) -> ClientProfile:
    timeout_raw = raw.get("timeout_seconds", 8.0)
    timeout = float(timeout_raw)
    if timeout <= 0:
        raise ValueError("timeout_seconds must be > 0")
    return ClientProfile(
        profile_id=_clean_text(raw.get("profile_id") or raw.get("id") or "default", max_chars=80),
        base_url=_normalize_base_url(raw.get("base_url")),
        auth_mode=_clean_text(raw.get("auth_mode") or "session_token", max_chars=40).lower(),
        environment=_clean_text(raw.get("environment") or "local", max_chars=40).lower(),
        auth_token=_clean_text(raw.get("auth_token"), max_chars=400) or None,
        timeout_seconds=min(timeout, 60.0),
    )


def sanitize_profile_for_persistence(profile: ClientProfile | dict[str, Any]) -> dict[str, Any]:
    if isinstance(profile, dict):
        profile = build_client_profile(profile)
    return {
        "profile_id": profile.profile_id,
        "base_url": profile.base_url,
        "auth_mode": profile.auth_mode,
        "environment": profile.environment,
        "timeout_seconds": profile.timeout_seconds,
    }


def redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    return _SECRET_INLINE.sub(r"\1=***", text)


def contains_secret_key(name: str) -> bool:
    key = str(name or "").strip().lower()
    return any(part in key for part in _SECRET_KEY_PARTS)


def resolve_session_auth_token(base_url: str, *, auth_mode: str, auth_token: str | None, timeout_seconds: float) -> str | None:
    if auth_token:
        return _clean_text(auth_token, max_chars=400) or None
    mode = _clean_text(auth_mode or "session_token", max_chars=40).lower()
    if mode != "session_token":
        return None

    env_token = _clean_text(os.environ.get("ANANTA_AUTH_TOKEN"), max_chars=400)
    if env_token:
        return env_token

    username = _clean_text(
        os.environ.get("ANANTA_USER")
        or os.environ.get("INITIAL_ADMIN_USER")
        or "admin",
        max_chars=120,
    )
    password = str(
        os.environ.get("ANANTA_PASSWORD")
        or os.environ.get("INITIAL_ADMIN_PASSWORD")
        or "admin"
    )
    request = urllib.request.Request(
        f"{_normalize_base_url(base_url)}/login",
        data=json.dumps({"username": username, "password": password}).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, min(float(timeout_seconds), 60.0))) as response:
            payload = json.loads(response.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        raise PermissionError(f"login_failed:{int(exc.code)}") from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(str(exc)) from exc

    data = payload.get("data") if isinstance(payload, dict) else {}
    token = _clean_text((data or {}).get("access_token"), max_chars=400)
    if not token:
        raise PermissionError("login_failed:missing_access_token")
    return token
