from __future__ import annotations

import re
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
