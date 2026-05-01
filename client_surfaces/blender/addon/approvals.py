from __future__ import annotations


def approval_state(item: dict) -> str:
    return str((item or {}).get("state") or (item or {}).get("status") or "pending")


def normalize_approvals(items: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    for item in list(items or []):
        payload = dict(item or {})
        payload.setdefault("id", payload.get("approval_id") or "")
        payload.setdefault("state", approval_state(payload))
        payload.setdefault("risk", payload.get("risk") or "unknown")
        payload.setdefault("action_text", payload.get("action_text") or payload.get("action_id") or "Review requested action")
        normalized.append(payload)
    return normalized


def build_approval_decision_payload(*, approval_id: str, decision: str, scope: dict | None = None) -> dict:
    decision_value = str(decision or "").strip().lower()
    if decision_value == "deny":
        decision_value = "reject"
    return {
        "approval_id": str(approval_id or "").strip(),
        "decision": decision_value,
        "scope": dict(scope or {}),
    }
