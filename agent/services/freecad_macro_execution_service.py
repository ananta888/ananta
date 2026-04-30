from __future__ import annotations

import hashlib


def execute_macro_if_approved(*, macro_text: str, approved: bool, approval_id: str | None, correlation_id: str) -> dict:
    script_hash = hashlib.sha256((macro_text or "").encode("utf-8")).hexdigest()
    if not approved:
        return {
            "status": "blocked",
            "reason": "approval_required",
            "approval_id": approval_id,
            "correlation_id": correlation_id,
            "script_hash": script_hash,
        }
    return {
        "status": "completed",
        "approval_id": approval_id,
        "correlation_id": correlation_id,
        "script_hash": script_hash,
        "stdout": "macro execution simulated",
        "stderr": "",
    }
