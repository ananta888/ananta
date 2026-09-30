"""Shared wire projection, digests, cursors and scope helpers of the Source Control API runtime.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)
from agent.services.source_index_lifecycle_service import (
    SourceIndexLifecycleScope,
)


# Keep the established log channel of the API runtime module.
_LOG = logging.getLogger("agent.services.source_control_api_runtime")
SENSITIVE_SENSITIVITIES = frozenset({"secret", "credential", "security_sensitive"})


class SourceControlApiRuntimeError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


def wire(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [wire(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return wire(to_dict())
    to_wire = getattr(value, "to_wire", None)
    if callable(to_wire):
        return wire(to_wire())
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return wire(model_dump(mode="json", by_alias=True))
    if is_dataclass(value):
        return wire(asdict(value))
    raise SourceControlApiRuntimeError(
        "source_control_projection_invalid", status_code=500
    )


def wire_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            wire(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


def iso_timestamp(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceControlApiRuntimeError("cursor_invalid") from exc
    if not decoded or len(decoded) > 160:
        raise SourceControlApiRuntimeError("cursor_invalid")
    return decoded


def runtime_principal(value: object) -> SourceControlPrincipal:
    return SourceControlPrincipal(
        subject_id=str(getattr(value, "subject_id")),
        tenant_id=str(getattr(value, "tenant_id")),
        project_id=str(getattr(value, "project_id")),
        roles=frozenset(str(role) for role in getattr(value, "roles", ())),
    )


def lifecycle_scope(value: object) -> SourceIndexLifecycleScope:
    actor = runtime_principal(value)
    return SourceIndexLifecycleScope(
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        actor_id=actor.subject_id,
        roles=actor.roles,
    )


def operation_key(
    namespace: str, tenant_id: str, idempotency_key: str
) -> str:
    digest = hashlib.sha256(
        f"{namespace}\0{tenant_id}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"{namespace}_{digest}"


class LifecycleAuditLog:
    def record(self, **event: object) -> None:
        _LOG.info(
            "source_control_lifecycle_audit %s",
            json.dumps(wire(event), sort_keys=True, ensure_ascii=True),
        )


def require_dependency(value: object | None, reason_code: str):
    """Return an optional runtime dependency or fail with 503."""

    if value is None:
        raise SourceControlApiRuntimeError(reason_code, status_code=503)
    return value
