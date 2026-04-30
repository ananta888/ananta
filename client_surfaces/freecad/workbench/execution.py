from __future__ import annotations

from typing import Any

from client_surfaces.freecad.bridge.ananta_freecad_bridge import (
    build_execution_report,
    build_freecad_bridge_envelope,
    validate_freecad_bridge_envelope,
)
from client_surfaces.freecad.workbench.client import FreecadHubClient


def execute_approved_macro(
    client: FreecadHubClient,
    *,
    script_hash: str,
    session_id: str,
    correlation_id: str,
    approval_id: str | None,
    macro_text: str,
) -> dict[str, Any]:
    envelope = build_freecad_bridge_envelope(
        capability_id="freecad.macro.execute",
        action_id="execute_macro",
        payload={"macro_text": macro_text},
        session_id=session_id,
        correlation_id=correlation_id,
        approval_id=approval_id,
        script_hash=script_hash,
    )
    errors = validate_freecad_bridge_envelope(envelope)
    if errors:
        return {"status": "blocked", "reason": "approval_required", "errors": errors, "report": build_execution_report(correlation_id=correlation_id, script_hash=script_hash, status="blocked")}
    response = client.execute_macro(envelope)
    return {"status": str(response.get("status") or "degraded"), "response": response, "report": build_execution_report(correlation_id=correlation_id, script_hash=script_hash, status=str(response.get("status") or "degraded"))}
