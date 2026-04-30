"""Thin envelope helpers for approval-gated FreeCAD actions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

BRIDGE_ENVELOPE_SCHEMA = "freecad_bridge_envelope_v1"


def build_freecad_bridge_envelope(
    *,
    capability_id: str,
    action_id: str,
    payload: dict[str, Any] | None,
    session_id: str,
    correlation_id: str,
    approval_id: str | None = None,
    script_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": BRIDGE_ENVELOPE_SCHEMA,
        "domain_id": "freecad",
        "capability_id": str(capability_id or "").strip(),
        "action_id": str(action_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "correlation_id": str(correlation_id or "").strip(),
        "approval_id": str(approval_id or "").strip() or None,
        "script_hash": str(script_hash or "").strip() or None,
        "payload": dict(payload or {}),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def validate_freecad_bridge_envelope(envelope: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    required = ("schema", "domain_id", "capability_id", "action_id", "session_id", "correlation_id", "payload")
    for field in required:
        value = envelope.get(field)
        if field == "payload":
            if not isinstance(value, dict):
                problems.append("field payload must be an object")
        elif not str(value or "").strip():
            problems.append(f"field {field} must not be empty")
    if str(envelope.get("schema") or "") != BRIDGE_ENVELOPE_SCHEMA:
        problems.append(f"schema must be {BRIDGE_ENVELOPE_SCHEMA}")
    if str(envelope.get("domain_id") or "") != "freecad":
        problems.append("domain_id must be freecad")
    if str(envelope.get("capability_id") or "") == "freecad.macro.execute":
        if not str(envelope.get("approval_id") or "").strip():
            problems.append("approval_id required for freecad.macro.execute")
        if not str(envelope.get("script_hash") or "").strip():
            problems.append("script_hash required for freecad.macro.execute")
    return problems


def build_execution_report(*, correlation_id: str, script_hash: str, status: str) -> dict[str, str]:
    return {"correlation_id": correlation_id, "script_hash": script_hash, "status": status}
