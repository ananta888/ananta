from __future__ import annotations


def mutation_wrapper(name: str, payload: dict) -> dict:
    return {"mutation": name, "payload": dict(payload or {}), "approval_required": True}
