from __future__ import annotations


def build_panel_state(runtime_state: dict | None = None) -> dict:
    state = dict(runtime_state or {})
    problems = list(state.get("problems") or [])
    return {
        "sections": ["connection", "goal", "context", "tasks", "artifacts", "approvals"],
        "state": state.get("state") or "degraded",
        "warnings": problems,
        "empty_states": {
            "tasks": not list(state.get("cached_tasks") or []),
            "artifacts": not list(state.get("cached_artifacts") or []),
            "approvals": not list(state.get("cached_approvals") or []),
        },
    }
