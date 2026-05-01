from __future__ import annotations


def mutation_wrapper(name: str, payload: dict) -> dict:
    return {"mutation": str(name or "").strip(), "payload": dict(payload or {}), "approval_required": True}


def build_mutation_plan(*, action: str, targets: list[str], parameters: dict | None = None) -> dict:
    return {
        "mode": "plan_only",
        "action": str(action or "").strip(),
        "targets": [str(item).strip() for item in list(targets or []) if str(item).strip()],
        "parameters": dict(parameters or {}),
        "approval_required": True,
        "expected_side_effects": ["scene_mutation"],
    }
