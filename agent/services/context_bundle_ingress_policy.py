"""Protect Hub-owned retrieval context at generic external task APIs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RESERVED_CONTEXT_BUNDLE_INGRESS_REASON = "context_bundle_reserved_ingress_forbidden"
_CONTEXT_BUNDLE_ID_KEY = "context_bundle_id"
_CONTEXT_PAYLOAD_KEY = "context"
_SCIENTIFIC_SKILL_KEY = "scientific_skill"


def _is_supplied(value: Any) -> bool:
    return value is not None


def find_reserved_context_bundle_marker(
    payload: Mapping[str, Any] | None,
) -> str | None:
    """Return a Hub-owned context field supplied at an external boundary."""

    values = payload if isinstance(payload, Mapping) else {}
    if _CONTEXT_BUNDLE_ID_KEY in values and _is_supplied(values.get(_CONTEXT_BUNDLE_ID_KEY)):
        return _CONTEXT_BUNDLE_ID_KEY
    worker_context = values.get("worker_execution_context")
    if not isinstance(worker_context, Mapping):
        return None
    if _CONTEXT_BUNDLE_ID_KEY in worker_context and _is_supplied(worker_context.get(_CONTEXT_BUNDLE_ID_KEY)):
        return f"worker_execution_context.{_CONTEXT_BUNDLE_ID_KEY}"
    if _CONTEXT_PAYLOAD_KEY in worker_context and _is_supplied(worker_context.get(_CONTEXT_PAYLOAD_KEY)):
        return f"worker_execution_context.{_CONTEXT_PAYLOAD_KEY}"
    # Skill projections are built by the Hub from an admitted pinned catalog
    # entry. External task payloads must not turn untrusted SKILL.md or paper
    # text into tool, policy, or approval authority.
    if _SCIENTIFIC_SKILL_KEY in worker_context and _is_supplied(worker_context.get(_SCIENTIFIC_SKILL_KEY)):
        return f"worker_execution_context.{_SCIENTIFIC_SKILL_KEY}"
    return None


def reserved_context_bundle_ingress_error(marker: str) -> dict[str, Any]:
    return {
        "error": RESERVED_CONTEXT_BUNDLE_INGRESS_REASON,
        "code": 403,
        "data": {
            "reason_code": RESERVED_CONTEXT_BUNDLE_INGRESS_REASON,
            "reserved_field": marker,
        },
    }


def preserve_hub_context_bundle_fields(
    *,
    existing_task: Mapping[str, Any],
    update_data: dict[str, Any],
) -> None:
    """Keep Hub-built context fields when an external patch replaces metadata."""

    if "worker_execution_context" not in update_data:
        return
    existing_context = existing_task.get("worker_execution_context")
    incoming_context = update_data.get("worker_execution_context")
    if not isinstance(existing_context, Mapping):
        return
    normalized_incoming = dict(incoming_context) if isinstance(incoming_context, Mapping) else {}
    for key in (_CONTEXT_BUNDLE_ID_KEY, _CONTEXT_PAYLOAD_KEY):
        if key in existing_context:
            normalized_incoming[key] = existing_context[key]
    update_data["worker_execution_context"] = normalized_incoming


__all__ = [
    "RESERVED_CONTEXT_BUNDLE_INGRESS_REASON",
    "find_reserved_context_bundle_marker",
    "preserve_hub_context_bundle_fields",
    "reserved_context_bundle_ingress_error",
]
