from __future__ import annotations

from client_surfaces.blender.addon.health import build_runtime_state, evaluate_health
from client_surfaces.blender.addon.tasks import update_task_cache


def test_runtime_state_covers_degraded_and_cached_lists() -> None:
    state = build_runtime_state(
        connected=False,
        state="unauthorized",
        problems=["auth_failed"],
        tasks=[{"id": "task-1"}],
        artifacts=[{"id": "artifact-1"}],
        approvals=[{"id": "approval-1"}],
    )

    assert state["state"] == "unauthorized"
    assert state["cached_tasks"][0]["id"] == "task-1"
    assert evaluate_health(False, problems=["auth_failed"])["state"] == "degraded"


def test_failed_refresh_keeps_previous_task_cache() -> None:
    previous = [{"id": "task-1", "status": "blocked"}]
    assert update_task_cache(previous, None)[0]["id"] == "task-1"
