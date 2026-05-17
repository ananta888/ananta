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
