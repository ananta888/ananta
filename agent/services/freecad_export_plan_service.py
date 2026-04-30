from __future__ import annotations


def build_export_plan(*, fmt: str, target_path: str, selection_only: bool = False) -> dict:
    normalized_fmt = str(fmt or "STEP").upper()
    return {
        "format": normalized_fmt,
        "target_path": str(target_path or ""),
        "selection_only": bool(selection_only),
        "approval_required": True,
        "execution_mode": "plan_only",
    }
