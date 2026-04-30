from __future__ import annotations


def build_panel_state() -> dict:
    return {
        "sections": ["connection", "goal", "context", "tasks", "artifacts", "approvals"],
        "warnings": [],
    }
