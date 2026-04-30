from __future__ import annotations


def execute_approved_action(*, approved: bool, script_hash: str, correlation_id: str) -> dict:
    if not approved:
        return {"status":"blocked","reason":"approval_required"}
    return {"status":"completed","script_hash":script_hash,"correlation_id":correlation_id}
