"""Canonical, fail-closed permissions for Pair View share sessions.

The Hub owns this translation boundary.  Callers never authorise against legacy
names directly: legacy keys are accepted only while the v0 migration window is
open and are immediately reduced to the v1 canonical shape.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

PERMISSION_CONTRACT_VERSION = 1
LEGACY_ALIAS_EXPIRES_AT = 1_798_761_600.0  # 2027-01-01T00:00:00Z

CANONICAL_PERMISSION_KEYS: tuple[str, ...] = (
    "chat",
    "view_tui",
    "remote_cursor",
    "artifact_share",
    "remote_control",
)

DEFAULT_PERMISSIONS: dict[str, bool] = {
    "chat": True,
    "view_tui": False,
    "remote_cursor": False,
    "artifact_share": False,
    "remote_control": False,
}

# v0 Angular names.  ``annotation`` was never represented independently by
# the Hub, so it is deliberately reduced to the already explicit
# ``artifact_share`` permission instead of creating a hidden write grant.
LEGACY_PERMISSION_ALIASES: dict[str, str] = {
    "cursor": "remote_cursor",
    "control": "remote_control",
    "artifact_view": "artifact_share",
    "annotation": "artifact_share",
}


class PermissionContractError(ValueError):
    """Stable public validation error for an invalid permission document."""

    def __init__(self, reason_code: str, *, field: str | None = None) -> None:
        self.reason_code = reason_code
        self.field = field
        super().__init__(reason_code if field is None else f"{reason_code}:{field}")


@dataclass(frozen=True)
class NormalizedPermissions:
    version: int
    values: dict[str, bool]
    legacy_aliases_used: tuple[str, ...] = ()


def normalize_share_permissions(
    raw: Mapping[str, Any] | None,
    *,
    now: float | None = None,
    allow_legacy: bool = True,
) -> NormalizedPermissions:
    """Return the canonical v1 shape or reject the entire document.

    Exact booleans are required.  In particular, strings such as ``"false"``
    must not become truthy grants.  Supplying an alias and its canonical key is
    allowed only when both values are identical.
    """

    if raw is None:
        return NormalizedPermissions(PERMISSION_CONTRACT_VERSION, dict(DEFAULT_PERMISSIONS))
    if not isinstance(raw, Mapping):
        raise PermissionContractError("permissions_invalid_type")

    timestamp = time.time() if now is None else float(now)
    out = dict(DEFAULT_PERMISSIONS)
    assigned: dict[str, tuple[bool, str]] = {}
    aliases_used: list[str] = []

    for source_key, raw_value in raw.items():
        if not isinstance(source_key, str):
            raise PermissionContractError("permission_unknown", field=str(source_key))
        if type(raw_value) is not bool:  # bool is intentionally exact here.
            raise PermissionContractError("permission_value_not_boolean", field=source_key)

        if source_key in CANONICAL_PERMISSION_KEYS:
            canonical_key = source_key
        elif source_key in LEGACY_PERMISSION_ALIASES:
            if not allow_legacy or timestamp >= LEGACY_ALIAS_EXPIRES_AT:
                raise PermissionContractError("permission_alias_expired", field=source_key)
            canonical_key = LEGACY_PERMISSION_ALIASES[source_key]
            aliases_used.append(source_key)
        else:
            raise PermissionContractError("permission_unknown", field=source_key)

        previous = assigned.get(canonical_key)
        if previous is not None and previous[0] is not raw_value:
            raise PermissionContractError("permission_conflict", field=canonical_key)
        assigned[canonical_key] = (raw_value, source_key)
        out[canonical_key] = raw_value

    return NormalizedPermissions(
        PERMISSION_CONTRACT_VERSION,
        out,
        tuple(sorted(set(aliases_used))),
    )


class ShareSessionPermissionService:
    """Small Hub-side authorization facade with explicit cache invalidation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[str, dict[str, bool]]] = {}

    def normalize(self, raw: Mapping[str, Any] | None) -> NormalizedPermissions:
        return normalize_share_permissions(raw)

    def effective(self, session_id: str, raw: Mapping[str, Any] | None) -> dict[str, bool]:
        normalized = self.normalize(raw).values
        digest = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self._lock:
            cached = self._cache.get(session_id)
            if cached is not None and cached[0] == digest:
                return dict(cached[1])
            self._cache[session_id] = (digest, dict(normalized))
        return dict(normalized)

    def allows(self, session_id: str, raw: Mapping[str, Any] | None, permission: str) -> bool:
        if permission not in CANONICAL_PERMISSION_KEYS:
            return False
        return self.effective(session_id, raw).get(permission) is True

    def invalidate(self, session_id: str) -> None:
        with self._lock:
            self._cache.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_SERVICE = ShareSessionPermissionService()


def get_share_session_permission_service() -> ShareSessionPermissionService:
    return _SERVICE
