"""Exact Hub capability for rebinding one pending Recovery approval."""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

RECOVERY_SOURCE_APPROVAL_STATE_SCHEMA = (
    "ananta.task_recovery_state.v1"
)
RECOVERY_SOURCE_APPROVAL_STATE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "plan_id",
        "approval_request_id",
        "recovery_key",
        "recovery_depth",
        "node_count",
        "team_id",
    }
)


@dataclass(frozen=True)
class RecoverySourceApprovalRebindWriteAuthority:
    task_id: str
    current_state_json: str
    proposed_state_json: str


_ACTIVE_AUTHORITY: ContextVar[
    RecoverySourceApprovalRebindWriteAuthority | None
] = ContextVar(
    "ananta_recovery_source_approval_rebind_write_authority",
    default=None,
)


def _identifier(
    value: Any,
    *,
    optional: bool = False,
) -> str:
    normalized = str(value or "").strip()
    if (
        (not optional and not normalized)
        or len(normalized.encode("utf-8")) > 256
    ):
        raise ValueError(
            "recovery_source_approval_rebind_authority_invalid"
        )
    return normalized


def _state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(
            "recovery_source_approval_rebind_authority_invalid"
        )
    state = dict(value)
    if (
        set(state) != RECOVERY_SOURCE_APPROVAL_STATE_FIELDS
        or state.get("schema")
        != RECOVERY_SOURCE_APPROVAL_STATE_SCHEMA
        or state.get("status") != "pending_approval"
        or isinstance(state.get("recovery_depth"), bool)
        or state.get("recovery_depth") != 1
        or isinstance(state.get("node_count"), bool)
        or not isinstance(state.get("node_count"), int)
        or not 0 <= int(state["node_count"]) <= 10_000
    ):
        raise ValueError(
            "recovery_source_approval_rebind_authority_invalid"
        )
    for key, optional in (
        ("plan_id", False),
        ("approval_request_id", False),
        ("recovery_key", False),
        ("team_id", True),
    ):
        normalized = _identifier(
            state.get(key),
            optional=optional,
        )
        if state.get(key) != normalized:
            raise ValueError(
                "recovery_source_approval_rebind_authority_invalid"
            )
    return state


def _authority(
    *,
    task_id: str,
    current_state: Any,
    proposed_state: Any,
) -> RecoverySourceApprovalRebindWriteAuthority:
    normalized_task_id = _identifier(task_id)
    current = _state(current_state)
    proposed = _state(proposed_state)
    current_approval_id = _identifier(
        current.get("approval_request_id")
    )
    proposed_approval_id = _identifier(
        proposed.get("approval_request_id")
    )
    expected = {
        **current,
        "approval_request_id": proposed_approval_id,
    }
    if (
        current_approval_id == proposed_approval_id
        or proposed != expected
    ):
        raise ValueError(
            "recovery_source_approval_rebind_authority_invalid"
        )
    return RecoverySourceApprovalRebindWriteAuthority(
        task_id=normalized_task_id,
        current_state_json=json.dumps(
            current,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        proposed_state_json=json.dumps(
            proposed,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


@contextmanager
def authorize_recovery_source_approval_rebind_write(
    *,
    task_id: str,
    current_state: Any,
    proposed_state: Any,
) -> Iterator[None]:
    authority = _authority(
        task_id=task_id,
        current_state=current_state,
        proposed_state=proposed_state,
    )
    token = _ACTIVE_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _ACTIVE_AUTHORITY.reset(token)


def recovery_source_approval_rebind_write_authorized(
    *,
    task_id: str,
    current_state: Any,
    proposed_state: Any,
) -> bool:
    try:
        expected = _authority(
            task_id=task_id,
            current_state=current_state,
            proposed_state=proposed_state,
        )
    except (TypeError, ValueError):
        return False
    return _ACTIVE_AUTHORITY.get() == expected


__all__ = [
    "RECOVERY_SOURCE_APPROVAL_STATE_FIELDS",
    "RECOVERY_SOURCE_APPROVAL_STATE_SCHEMA",
    "RecoverySourceApprovalRebindWriteAuthority",
    "authorize_recovery_source_approval_rebind_write",
    "recovery_source_approval_rebind_write_authorized",
]
