from __future__ import annotations


def build_macro_plan(*, objective: str, context_summary: dict | None = None) -> dict:
    summary = str(objective or "").strip()
    return {
        "mode": "dry_run",
        "trusted": False,
        "objective": summary,
        "macro_outline": [
            "Open active document",
            "Validate object references",
            "Apply bounded operation sequence",
            "Recompute document",
        ],
        "safety_notes": [
            "macro remains untrusted until approval",
            "no execution in planning stage",
        ],
        "context_summary": dict(context_summary or {}),
    }
