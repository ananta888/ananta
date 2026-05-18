from __future__ import annotations


def test_reconcile_dependencies_unblocks_tasks_without_valid_dependencies(app):
    from agent.db_models import TaskDB
    from agent.services.repository_registry import get_repository_registry
    from agent.services.task_queue_service import get_task_queue_service

    with app.app_context():
        repos = get_repository_registry()
        task_repo = repos.task_repo
        task_repo.save(
            TaskDB(
                id="tq-invalid-deps-1",
                title="Invalid dependency task",
                status="blocked_by_dependency",
                depends_on=["", "   ", "tq-invalid-deps-1"],
            )
        )

        svc = get_task_queue_service()
        transitions = svc.reconcile_dependencies(
            tasks=task_repo.get_all(),
            dependency_resolver=lambda task: list(getattr(task, "depends_on", None) or []),
        )

        updated = task_repo.get_by_id("tq-invalid-deps-1")
        assert updated is not None
        assert str(getattr(updated, "status", "")).lower() == "todo"
        assert any(
            str(item.get("task_id") or "") == "tq-invalid-deps-1"
            and str(item.get("event_type") or "") == "dependency_unblocked"
            and str(item.get("reason") or "") == "no_valid_dependencies"
            for item in transitions
            if isinstance(item, dict)
        )


# APR-003: no-candidate reason classification
def test_classify_no_candidate_reason_no_tasks():
    from agent.routes.tasks.autopilot_dispatch_policy import classify_no_candidate_reason
    reason = classify_no_candidate_reason(all_tasks=[], workers_available_count=2)
    assert reason == "no_tasks"


def test_classify_no_candidate_reason_all_terminal():
    from unittest.mock import MagicMock
    from agent.routes.tasks.autopilot_dispatch_policy import classify_no_candidate_reason

    tasks = [MagicMock(status="completed"), MagicMock(status="failed")]
    reason = classify_no_candidate_reason(all_tasks=tasks, workers_available_count=2)
    assert reason == "all_terminal"


def test_classify_no_candidate_reason_all_blocked_by_dependency():
    from unittest.mock import MagicMock
    from agent.routes.tasks.autopilot_dispatch_policy import classify_no_candidate_reason

    tasks = [MagicMock(status="blocked_by_dependency"), MagicMock(status="completed")]
    reason = classify_no_candidate_reason(all_tasks=tasks, workers_available_count=2)
    assert reason == "all_blocked_by_dependency"


def test_classify_no_candidate_reason_no_workers():
    from unittest.mock import MagicMock
    from agent.routes.tasks.autopilot_dispatch_policy import classify_no_candidate_reason

    tasks = [MagicMock(status="todo")]
    reason = classify_no_candidate_reason(all_tasks=tasks, workers_available_count=0)
    assert reason == "no_workers_available"


def test_classify_no_candidate_reason_policy_or_state_blocked():
    from unittest.mock import MagicMock
    from agent.routes.tasks.autopilot_dispatch_policy import classify_no_candidate_reason

    tasks = [MagicMock(status="todo"), MagicMock(status="waiting_for_review")]
    reason = classify_no_candidate_reason(all_tasks=tasks, workers_available_count=3)
    assert reason == "policy_or_state_blocked"
