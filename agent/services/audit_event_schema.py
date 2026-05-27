from __future__ import annotations

import time
from typing import Any


CANONICAL_AUDIT_EVENT_SCHEMA: dict[str, Any] = {
    "schema": "canonical_audit_event.v1",
    "required_fields": [
        "trace_id",
        "task_id",
        "actor",
        "role",
        "policy_version",
        "operation_type",
        "target",
        "outcome",
        "timestamp",
    ],
}


def classify_context_classes(*, details: dict[str, Any] | None, task_metadata: dict[str, Any] | None) -> list[str]:
    from_details = list((details or {}).get("context_classes") or [])
    from_task = list((task_metadata or {}).get("context_classes") or [])
    merged: list[str] = []
    seen: set[str] = set()
    for value in from_details + from_task:
        item = str(value or "").strip().lower()
        if not item or item in seen:
            continue
        seen.add(item)
        merged.append(item)
    return merged


def build_canonical_audit_event(
    *,
    trace_id: str | None,
    task_id: str | None,
    actor: str,
    role: str,
    policy_version: str,
    operation_type: str,
    target: dict[str, Any],
    outcome: str,
    details: dict[str, Any] | None = None,
    parent_trace_id: str | None = None,
    context_classes: list[str] | None = None,
    prompt_bundle_class: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": CANONICAL_AUDIT_EVENT_SCHEMA["schema"],
        "trace_id": str(trace_id or "").strip() or None,
        "task_id": str(task_id or "").strip() or None,
        "actor": str(actor or "system"),
        "role": str(role or "system"),
        "policy_version": str(policy_version or "unknown"),
        "operation_type": str(operation_type or "unknown"),
        "target": dict(target or {}),
        "outcome": str(outcome or "unknown"),
        "timestamp": time.time(),
        "chain": {"parent_trace_id": str(parent_trace_id or "").strip() or None},
        "prompt_bundle_class": str(prompt_bundle_class or "unknown"),
        "context_classes": list(context_classes or []),
        "details": dict(details or {}),
    }
