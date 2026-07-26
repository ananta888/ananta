from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest


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


@pytest.mark.parametrize(
    "terminal_status",
    (
        "failed",
        "verification_failed",
        "cancelled",
        "aborted",
        "timeout",
        "skipped",
        "archived",
    ),
)
def test_recovery_dependency_terminal_status_fails_blocked_child(
    app,
    terminal_status,
):
    from agent.db_models import TaskDB
    from agent.services.repository_registry import (
        get_repository_registry,
    )
    from agent.services.task_queue_service import TaskQueueService

    suffix = terminal_status.replace("_", "-")
    source_id = f"recovery-source-{suffix}"
    dependency_id = f"recovery-dependency-{suffix}"
    dependent_id = f"recovery-dependent-{suffix}"
    with app.app_context():
        repos = get_repository_registry()
        repos.task_repo.save(
            TaskDB(
                id=source_id,
                status="blocked_by_dependency",
                status_reason_details={
                    "model_recovery": {
                        "schema": "task_model_recovery.v1",
                        "status": "materialized_waiting_for_children",
                    }
                },
            )
        )
        repos.task_repo.save(
            TaskDB(
                id=dependency_id,
                status=terminal_status,
                source_task_id=source_id,
                derivation_reason="goal_task_recovery",
            )
        )
        repos.task_repo.save(
            TaskDB(
                id=dependent_id,
                status="blocked_by_dependency",
                source_task_id=source_id,
                derivation_reason="goal_task_recovery",
                depends_on=[dependency_id],
            )
        )

        TaskQueueService().reconcile_dependencies(
            tasks=repos.task_repo.get_all(),
            dependency_resolver=lambda task: list(
                getattr(task, "depends_on", None) or []
            ),
        )

        dependent = repos.task_repo.get_by_id(dependent_id)
        assert dependent.status == "failed"
        marker = dependent.status_reason_details[
            "recovery_dependency_reconciliation"
        ]
        assert marker["task_id"] == dependent_id
        assert marker["source_task_id"] == source_id
        assert marker["previous_status"] == (
            "blocked_by_dependency"
        )
        assert marker["target_status"] == "failed"
        assert marker["reason_code"] == (
            "recovery_dependency_terminal"
        )
        assert marker["dependency_statuses"] == [
            {
                "task_id": dependency_id,
                "status": terminal_status,
            }
        ]
        assert marker["failed_dependency_ids"] == [
            dependency_id
        ]


def test_ordinary_child_with_source_uses_generic_dependency_failure(
    app,
):
    from agent.db_models import TaskDB
    from agent.services.repository_registry import (
        get_repository_registry,
    )
    from agent.services.task_queue_service import TaskQueueService

    source_id = "ordinary-hierarchy-source"
    dependency_id = "ordinary-hierarchy-dependency"
    dependent_id = "ordinary-hierarchy-dependent"
    with app.app_context():
        repos = get_repository_registry()
        repos.task_repo.save(
            TaskDB(
                id=source_id,
                status="in_progress",
            )
        )
        repos.task_repo.save(
            TaskDB(
                id=dependency_id,
                status="failed",
            )
        )
        repos.task_repo.save(
            TaskDB(
                id=dependent_id,
                status="blocked_by_dependency",
                source_task_id=source_id,
                depends_on=[dependency_id],
            )
        )

        transitions = TaskQueueService().reconcile_dependencies(
            tasks=repos.task_repo.get_all(),
            dependency_resolver=lambda task: list(
                getattr(task, "depends_on", None) or []
            ),
        )

        dependent = repos.task_repo.get_by_id(dependent_id)
        assert dependent.status == "failed"
        assert dependent.status_reason_code == "dependency_terminal"
        assert (
            "recovery_dependency_reconciliation"
            not in dependent.status_reason_details
        )
        assert any(
            transition["task_id"] == dependent_id
            and transition["event_type"] == "dependency_failed"
            and transition["failed_dependency_ids"]
            == [dependency_id]
            for transition in transitions
        )


def test_missing_recovery_dependency_fails_source_instead_of_deadlocking(app):
    from agent.db_models import TaskDB
    from agent.services.repository_registry import (
        get_repository_registry,
    )
    from agent.services.task_queue_service import TaskQueueService

    source_id = "recovery-source-missing-dependency"
    with app.app_context():
        repos = get_repository_registry()
        repos.task_repo.save(
            TaskDB(
                id=source_id,
                status="blocked_by_dependency",
                depends_on=["deleted-recovery-child"],
                status_reason_details={
                    "model_recovery": {
                        "schema": "task_model_recovery.v1",
                        "status": "materialized_waiting_for_children",
                    }
                },
            )
        )

        transitions = TaskQueueService().reconcile_dependencies(
            tasks=repos.task_repo.get_all(),
            dependency_resolver=lambda task: list(
                getattr(task, "depends_on", None) or []
            ),
        )

        source = repos.task_repo.get_by_id(source_id)
        assert source.status == "verification_failed"
        assert source.status_reason_code == (
            "recovery_source_binding_incomplete"
        )
        assert any(
            transition["task_id"] == source_id
            and transition["event_type"]
            == "recovery_source_finalized"
            and transition["reason"]
            == "recovery_source_binding_incomplete"
            for transition in transitions
        )


@pytest.mark.parametrize(
    "depends_on",
    (
        [],
        ["", "   ", "recovery-source-no-valid-dependencies"],
    ),
    ids=("empty", "invalid-only"),
)
def test_recovery_source_without_valid_dependencies_fails_closed(
    app,
    depends_on,
):
    from agent.db_models import TaskDB
    from agent.services.repository_registry import (
        get_repository_registry,
    )
    from agent.services.task_queue_service import TaskQueueService

    source_id = "recovery-source-no-valid-dependencies"
    with app.app_context():
        repos = get_repository_registry()
        repos.task_repo.save(
            TaskDB(
                id=source_id,
                status="blocked_by_dependency",
                depends_on=depends_on,
                status_reason_details={
                    "model_recovery": {
                        "schema": "task_model_recovery.v1",
                        "status": "materialized_waiting_for_children",
                    }
                },
            )
        )

        transitions = TaskQueueService().reconcile_dependencies(
            tasks=repos.task_repo.get_all(),
            dependency_resolver=lambda task: list(
                getattr(task, "depends_on", None) or []
            ),
        )

        source = repos.task_repo.get_by_id(source_id)
        assert source.status == "verification_failed"
        assert source.status_reason_code == (
            "recovery_source_binding_incomplete"
        )
        assert any(
            transition["task_id"] == source_id
            and transition["event_type"]
            == "recovery_source_finalized"
            and transition["depends_on"] == []
            and transition["reason"]
            == "recovery_source_binding_incomplete"
            for transition in transitions
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


def test_parallel_claims_have_exactly_one_winner(
    app,
    monkeypatch,
    tmp_path,
):
    from sqlmodel import SQLModel, create_engine

    from agent.db_models import TaskDB
    from agent.services.repository_registry import (
        get_repository_registry,
    )
    from agent.services.task_queue_service import TaskQueueService

    race_engine = create_engine(
        f"sqlite:///{tmp_path / 'parallel-claim-race.db'}",
        connect_args={
            "check_same_thread": False,
            "timeout": 10,
        },
    )
    SQLModel.metadata.create_all(race_engine)
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: race_engine,
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service._task_engine",
        lambda: race_engine,
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service.request_autopilot_wake",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "agent.services.execution_audit_service."
        "get_execution_audit_service",
        lambda: SimpleNamespace(
            emit_workflow_transition=lambda **_kwargs: None,
            emit_write_operation=lambda **_kwargs: None,
        ),
    )

    with app.app_context():
        repos = get_repository_registry()
        repos.task_repo.save(
            TaskDB(
                id="parallel-claim-task",
                status="todo",
            )
        )
    barrier = threading.Barrier(2)

    def claim(worker: str) -> bool:
        barrier.wait(timeout=5)
        return TaskQueueService().claim_task(
            task_id="parallel-claim-task",
            agent_url=worker,
            lease_until=time.time() + 30,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                claim,
                ["http://worker-a", "http://worker-b"],
            )
        )
    assert results.count(True) == 1
    assert results.count(False) == 1


def test_claim_denies_task_whose_parent_goal_is_archived(app):
    from agent.db_models import GoalDB, TaskDB
    from agent.services.repository_registry import (
        get_repository_registry,
    )
    from agent.services.task_queue_service import TaskQueueService

    with app.app_context():
        repos = get_repository_registry()
        repos.goal_repo.save(
            GoalDB(
                id="archived-claim-goal",
                goal="Already archived",
                status="archived",
            )
        )
        repos.task_repo.save(
            TaskDB(
                id="archived-goal-task",
                goal_id="archived-claim-goal",
                status="todo",
            )
        )
        assert (
            TaskQueueService().claim_task(
                task_id="archived-goal-task",
                agent_url="http://worker",
                lease_until=time.time() + 30,
            )
            is False
        )
        assert repos.task_repo.get_by_id(
            "archived-goal-task"
        ).status == "todo"


def test_terminal_goal_racing_materialization_leaves_no_runnable_task(
    app,
    monkeypatch,
    tmp_path,
):
    from sqlmodel import SQLModel, create_engine

    from agent.db_models import GoalDB
    from agent.services.lifecycle_service import (
        get_goal_lifecycle_service,
        get_task_lifecycle_service,
    )
    from agent.services.repository_registry import (
        get_repository_registry,
    )
    from agent.services.task_queue_service import TaskQueueService

    race_engine = create_engine(
        f"sqlite:///{tmp_path / 'goal-materialization-race.db'}",
        connect_args={
            "check_same_thread": False,
            "timeout": 10,
        },
    )
    SQLModel.metadata.create_all(race_engine)
    monkeypatch.setattr(
        "agent.repositories.tasks._engine",
        lambda: race_engine,
    )
    monkeypatch.setattr(
        "agent.repositories.goals._engine",
        lambda: race_engine,
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service._task_engine",
        lambda: race_engine,
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service.request_autopilot_wake",
        lambda *_args, **_kwargs: None,
    )

    with app.app_context():
        repos = get_repository_registry()
        repos.goal_repo.save(
            GoalDB(
                id="planner-terminal-race",
                goal="Race planner and archive",
                status="in_progress",
            )
        )
    barrier = threading.Barrier(2)
    errors: list[str] = []

    def materialize() -> None:
        barrier.wait(timeout=5)
        try:
            get_task_lifecycle_service().materialize_from_plan_node(
                task_id="planner-terminal-race-task",
                node=SimpleNamespace(
                    id="node-race",
                    title="Should not stay runnable",
                    description="Race-safe materialization",
                    priority="High",
                    rationale={
                        "task_kind": "coding",
                        "required_capabilities": [],
                    },
                    verification_spec={},
                ),
                team_id=None,
                goal_id="planner-terminal-race",
                goal_trace_id=None,
                plan_id="plan-race",
                parent_task_id=None,
                derivation_reason="goal_planning",
                derivation_depth=0,
                depends_on=[],
            )
        except RuntimeError as exc:
            errors.append(str(exc))

    def archive() -> None:
        barrier.wait(timeout=5)
        goal = get_repository_registry().goal_repo.get_by_id(
            "planner-terminal-race"
        )
        get_goal_lifecycle_service().transition_goal(
            goal,
            target_status="archived",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(materialize),
            pool.submit(archive),
        ]
        for future in futures:
            future.result(timeout=10)

    with app.app_context():
        repos = get_repository_registry()
        assert repos.goal_repo.get_by_id(
            "planner-terminal-race"
        ).status == "archived"
        task = repos.task_repo.get_by_id(
            "planner-terminal-race-task"
        )
        if task is not None:
            assert task.status in {
                "cancelled",
                "failed",
                "archived",
            }
            assert (
                TaskQueueService().claim_task(
                    task_id=task.id,
                    agent_url="http://worker",
                    lease_until=time.time() + 30,
                )
                is False
            )
