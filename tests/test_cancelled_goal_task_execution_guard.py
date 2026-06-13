from types import SimpleNamespace

from agent.services.task_scoped_execution_service import TaskScopedExecutionService


def test_terminal_parent_goal_guard_returns_skipped(monkeypatch):
    monkeypatch.setattr(
        "agent.services._task_scoped_runtime.get_repository_registry",
        lambda: SimpleNamespace(goal_repo=SimpleNamespace(get_by_id=lambda _gid: SimpleNamespace(status="cancelled"))),
    )
    monkeypatch.setattr("agent.services._task_scoped_runtime.update_local_task_status", lambda *a, **k: None)
    out = TaskScopedExecutionService._terminal_parent_goal_guard(
        tid="t1",
        task={"id": "t1", "goal_id": "g1", "status": "assigned"},
        phase="propose",
    )
    assert out is not None
    assert out.data["reason"] == "parent_goal_cancelled"


def test_terminal_parent_goal_guard_allows_active_goal(monkeypatch):
    monkeypatch.setattr(
        "agent.services._task_scoped_runtime.get_repository_registry",
        lambda: SimpleNamespace(goal_repo=SimpleNamespace(get_by_id=lambda _gid: SimpleNamespace(status="active"))),
    )
    out = TaskScopedExecutionService._terminal_parent_goal_guard(
        tid="t1",
        task={"id": "t1", "goal_id": "g1", "status": "assigned"},
        phase="execute",
    )
    assert out is None
