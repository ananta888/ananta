"""Rate-limit policy for mutation-heavy dashboard surfaces."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.rate_limit_service import (
    RateLimitService,
    get_rate_limit_service,
)


KANBAN_WRITE = "kanban_write"
MODEL_CATALOG_REFRESH = "model_catalog_refresh"
MODEL_DEFAULT_SELECTION = "model_default_selection"


@dataclass(frozen=True, slots=True)
class SurfaceRateLimit:
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class SurfaceRateLimitDecision:
    allowed: bool
    retry_after_seconds: int


_DEFAULTS: dict[str, SurfaceRateLimit] = {
    KANBAN_WRITE: SurfaceRateLimit(limit=120, window_seconds=60),
    MODEL_CATALOG_REFRESH: SurfaceRateLimit(limit=6, window_seconds=60),
    MODEL_DEFAULT_SELECTION: SurfaceRateLimit(limit=12, window_seconds=60),
}

_ENV_PREFIXES = {
    KANBAN_WRITE: "ANANTA_KANBAN_WRITE_RATE_LIMIT",
    MODEL_CATALOG_REFRESH: "ANANTA_MODEL_CATALOG_REFRESH_RATE_LIMIT",
    MODEL_DEFAULT_SELECTION: "ANANTA_MODEL_DEFAULT_SELECTION_RATE_LIMIT",
}


def _positive_int(value: Any, fallback: int, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    if parsed < 1 or parsed > maximum:
        return fallback
    return parsed


def _configured_policy(
    config: Mapping[str, Any],
    namespace: str,
) -> Mapping[str, Any]:
    configured = config.get("SURFACE_RATE_LIMITS", {})
    if isinstance(configured, Mapping):
        value = configured.get(namespace, {})
        if isinstance(value, Mapping):
            return value

    agent_config = config.get("AGENT_CONFIG")
    if isinstance(agent_config, Mapping):
        configured = agent_config.get("surface_rate_limits", {})
    else:
        configured = getattr(agent_config, "surface_rate_limits", {})
    if isinstance(configured, Mapping):
        value = configured.get(namespace, {})
        if isinstance(value, Mapping):
            return value
    return {}


def resolve_surface_rate_limit(
    config: Mapping[str, Any],
    namespace: str,
) -> SurfaceRateLimit:
    """Resolve a positive bounded policy from app config and environment."""

    default = _DEFAULTS[namespace]
    configured = _configured_policy(config, namespace)
    prefix = _ENV_PREFIXES[namespace]
    return SurfaceRateLimit(
        limit=_positive_int(
            os.getenv(prefix, configured.get("limit", default.limit)),
            default.limit,
            maximum=10_000,
        ),
        window_seconds=_positive_int(
            os.getenv(
                f"{prefix}_WINDOW_SECONDS",
                configured.get("window_seconds", default.window_seconds),
            ),
            default.window_seconds,
            maximum=3_600,
        ),
    )


def _claim(source: Any, *names: str) -> str | None:
    if isinstance(source, Mapping):
        for name in names:
            value = source.get(name)
            if value not in (None, ""):
                return str(value)
        return None
    for name in names:
        value = getattr(source, name, None)
        if value not in (None, ""):
            return str(value)
    return None


def rate_limit_subject(
    *,
    auth_payload: Any = None,
    user: Any = None,
    remote_addr: str | None = None,
) -> str:
    """Derive a stable, tenant-aware, non-PII identity key."""

    subject = _claim(
        auth_payload,
        "sub",
        "subject",
        "service_id",
        "client_id",
        "username",
    ) or _claim(user, "id", "sub", "username", "name")
    tenant = _claim(auth_payload, "tenant_id", "tenant") or _claim(
        user, "tenant_id", "tenant"
    )
    raw = f"{tenant or '-'}|{subject or remote_addr or 'anonymous'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SurfaceRateLimitPolicy:
    """Application policy around the shared Redis-first infrastructure limiter."""

    def __init__(self, limiter: RateLimitService | None = None) -> None:
        self._limiter = limiter or get_rate_limit_service()

    def consume(
        self,
        *,
        config: Mapping[str, Any],
        namespace: str,
        auth_payload: Any = None,
        user: Any = None,
        remote_addr: str | None = None,
    ) -> SurfaceRateLimitDecision:
        policy = resolve_surface_rate_limit(config, namespace)
        allowed = self._limiter.allow_request(
            namespace=namespace,
            subject=rate_limit_subject(
                auth_payload=auth_payload,
                user=user,
                remote_addr=remote_addr,
            ),
            limit=policy.limit,
            window_seconds=policy.window_seconds,
        )
        return SurfaceRateLimitDecision(
            allowed=allowed,
            retry_after_seconds=0 if allowed else policy.window_seconds,
        )

    def clear(self, namespace: str | None = None) -> None:
        if namespace is None:
            self._limiter.clear_all()
        else:
            self._limiter.clear_namespace(namespace)


surface_rate_limit_policy = SurfaceRateLimitPolicy()


def is_auth_disabled(auth_payload: Any) -> bool:
    return _claim(auth_payload, "auth_mode") == "auth_disabled"
