from __future__ import annotations


def execute_approved_action(
    *,
    approved: bool,
    script_hash: str,
    correlation_id: str,
    approval_id: str | None = None,
    capability_id: str = "blender.script.execute",
    targets: list[str] | None = None,
) -> dict:
    if not approved or not str(approval_id or "").strip():
        return {"status": "blocked", "reason": "approval_required", "correlation_id": correlation_id}
    return {
        "status": "completed",
        "script_hash": script_hash,
        "correlation_id": correlation_id,
        "approval_id": approval_id,
        "capability_id": capability_id,
        "targets": list(targets or []),
    }


def build_execution_request(*, approval_id: str, action: str, payload: dict, correlation_id: str) -> dict:
    return {
        "approval_id": str(approval_id or "").strip(),
        "action": str(action or "").strip(),
        "payload": dict(payload or {}),
        "correlation_id": str(correlation_id or "").strip(),
    }
