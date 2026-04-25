"""Thin envelope helpers for Blender bridge actions.

This module intentionally keeps orchestration outside Blender and only prepares
bounded action envelopes for hub-side policy routing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

BRIDGE_ENVELOPE_SCHEMA = "blender_bridge_envelope_v1"


def build_bridge_envelope(
    *,
    domain_id: str = "blender",
    capability_id: str,
    action_id: str,
    payload: dict[str, Any] | None = None,
    session_id: str,
) -> dict[str, Any]:
    """Create a normalized bridge envelope for domain action routing."""
    return {
        "schema": BRIDGE_ENVELOPE_SCHEMA,
        "domain_id": str(domain_id).strip(),
        "capability_id": str(capability_id).strip(),
        "action_id": str(action_id).strip(),
        "session_id": str(session_id).strip(),
        "payload": dict(payload or {}),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def validate_bridge_envelope(envelope: dict[str, Any]) -> list[str]:
    """Validate envelope shape before forwarding to hub APIs."""
    if not isinstance(envelope, dict):
        return ["envelope must be an object"]
    problems: list[str] = []
    required = ("schema", "domain_id", "capability_id", "action_id", "session_id", "payload")
    for field in required:
        if field not in envelope:
            problems.append(f"missing field: {field}")
            continue
        value = envelope[field]
        if field == "payload":
            if not isinstance(value, dict):
                problems.append("field payload must be an object")
        elif not str(value or "").strip():
            problems.append(f"field {field} must not be empty")
    if str(envelope.get("schema") or "").strip() != BRIDGE_ENVELOPE_SCHEMA:
        problems.append(f"schema must be {BRIDGE_ENVELOPE_SCHEMA}")
    if str(envelope.get("domain_id") or "").strip() != "blender":
        problems.append("domain_id must be blender")
    return problems
