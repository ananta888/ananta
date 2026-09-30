"""Pure idempotency-key scoping and replay comparison for Run-Control."""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Mapping

from agent.services.run_control_models import RunCommand, RunControlPrincipal


def idempotency_scope_key(
    key: str | None,
    *,
    principal: RunControlPrincipal,
    task_id: str | None,
    goal_id: str | None,
    run_id: str | None,
) -> str:
    if not key:
        return ""
    canonical = json.dumps(
        {
            "client_key": key,
            "subject_id": principal.subject_id,
            "tenant_id": principal.tenant_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    # Operation, resources and payload remain in the command fingerprint
    # comparison. Reusing one client key for a different request inside the
    # same principal scope is an explicit conflict, while another tenant or
    # subject has an independent key space.
    del task_id, goal_id, run_id
    return sha256(canonical).hexdigest()


def idempotency_key_ref(key: str | None) -> str:
    """Return a stable opaque audit reference, never the caller's raw key."""

    if not key:
        return ""
    digest = sha256(str(key).encode("utf-8")).hexdigest()
    return f"idempotency-sha256:{digest}"


def values_are_exact(left: Any, right: Any) -> bool:
    """Compare request payloads without Python's cross-type equality aliases."""

    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        if left.keys() != right.keys():
            return False
        return all(
            values_are_exact(left[key], right[key])
            for key in left
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            values_are_exact(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return bool(left == right)


def idempotency_mismatches(
    existing: RunCommand,
    *,
    command_type: str,
    task_id: str | None,
    goal_id: str | None,
    run_id: str | None,
    payload: dict[str, Any],
    requested_by: str,
    principal: RunControlPrincipal,
) -> tuple[str, ...]:
    values = {
        "command_type": (existing.type, command_type),
        "task_id": (existing.task_id, task_id),
        "goal_id": (existing.goal_id, goal_id),
        "run_id": (existing.run_id, run_id),
        "payload": (existing.payload, payload),
        "requested_by": (existing.requested_by, requested_by),
        "tenant_id": (existing.tenant_id, principal.tenant_id),
        "subject_id": (existing.subject_id, principal.subject_id),
    }
    return tuple(
        name
        for name, (left, right) in values.items()
        if not values_are_exact(left, right)
    )
