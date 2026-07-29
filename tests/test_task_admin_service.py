import contextlib
import threading
import time
from types import SimpleNamespace

import pytest

from agent.common.recovery_task_admin_write_boundary import (
    authorize_recovery_task_admin_write,
    recovery_task_admin_write_authorized,
)
from agent.db_models import GoalDB, PlanDB, TaskDB
from agent.services.task_admin_service import (
    RecoveryChildAdminMutationConflict,
    TaskAdminService,
)
from agent.services.vector_index_task_contracts import (
    VectorIndexTrustedScope,
)
from agent.services.vector_store_authorization_policy import (
    get_vector_store_authorization_policy,
)


def _persist_recovery_lineage(
    *,
    suffix: str,
    child_status: str,
    source_status: str = "blocked_by_dependency",
    goal_status: str = "executing",
    lease: dict | None = None,
) -> tuple[str, str, str, str]:
    from agent.services.repository_registry import (
        get_repository_registry,
    )

    repos = get_repository_registry()
    goal_id = f"goal-admin-{suffix}"
    plan_id = f"plan-admin-{suffix}"
    source_task_id = f"source-admin-{suffix}"
    child_task_id = f"child-admin-{suffix}"
    repos.goal_repo.save(
        GoalDB(
            id=goal_id,
            goal=f"Recovery admin guard {suffix}",
            status=goal_status,
        )
    )
    repos.plan_repo.save(
        PlanDB(
            id=plan_id,
            goal_id=goal_id,
            trace_id=f"trace-admin-{suffix}",
            status="materialized",
            planning_mode="model_exhaustion_recovery",
            rationale={"source_task_id": source_task_id},
        )
    )
    repos.task_repo.save(
        TaskDB(
            id=source_task_id,
            goal_id=goal_id,
            plan_id=None,
            status=source_status,
            status_reason_details={
                "model_recovery": {
                    "plan_id": plan_id,
                    "status": "materialized_waiting_for_children",
                    "created_task_ids": [child_task_id],
                }
            },
            depends_on=[child_task_id],
        )
    )
    details = {
        "model_recovery_release": {
            "plan_id": plan_id,
            "source_task_id": source_task_id,
            "goal_id": goal_id,
        }
    }
    if lease is not None:
        details["recovery_dispatch_lease"] = dict(lease)
    repos.task_repo.save(
        TaskDB(
            id=child_task_id,
            goal_id=goal_id,
            plan_id=plan_id,
            plan_node_id=f"node-admin-{suffix}",
            source_task_id=source_task_id,
            derivation_reason="goal_task_recovery",
            derivation_depth=1,
            status=child_status,
            status_reason_details=details,
        )
    )
    return goal_id, plan_id, source_task_id, child_task_id


def _active_recovery_dispatch_lease(
    *,
    task_id: str,
    source_task_id: str,
    goal_id: str,
    plan_id: str,
) -> dict:
    issued_at = time.time()
    return {
        "schema": "ananta.recovery_dispatch_lease.v1",
        "task_id": task_id,
        "token_digest": "a" * 64,
        "phase": "execute",
        "state": "active",
        "revision": 1,
        "issued_at": issued_at,
        "expires_at": issued_at + 300,
        "worker_url": "http://worker-admin-race:5000",
        "source_task_id": source_task_id,
        "goal_id": goal_id,
        "plan_id": plan_id,
        "release_epoch": "release-admin-race",
        "request_fingerprint": "b" * 64,
    }


def test_recovery_task_admin_write_authority_is_exactly_bound():
    authority = {
        "task_id": "source-admin-boundary",
        "source_task_id": "source-admin-boundary",
        "goal_id": "goal-admin-boundary",
        "action": "archive",
        "from_status": "blocked_by_dependency",
        "to_status": "cancelled",
    }

    assert not recovery_task_admin_write_authorized(**authority)
    with authorize_recovery_task_admin_write(**authority):
        assert recovery_task_admin_write_authorized(**authority)
        for field, mismatched_value in (
            ("task_id", "other-task"),
            ("source_task_id", "other-source"),
            ("goal_id", "other-goal"),
            ("action", "delete"),
            ("from_status", "todo"),
            ("to_status", "failed"),
        ):
            mismatched = {**authority, field: mismatched_value}
            assert not recovery_task_admin_write_authorized(
                **mismatched
            )
    assert not recovery_task_admin_write_authorized(**authority)


def test_detached_recovery_source_archive_transition_remains_denied(
    app,
):
    with app.app_context():
        goal_id, _plan_id, source_id, _child_id = (
            _persist_recovery_lineage(
                suffix="archive-authority",
                child_status="todo",
            )
        )
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repos = get_repository_registry()

        def _candidate() -> TaskDB:
            source = repos.task_repo.get_by_id(source_id)
            source.status = "cancelled"
            source.status_reason_code = "task_archived"
            source.updated_at = time.time()
            return source

        with pytest.raises(
            ValueError,
            match=(
                "recovery_source_finalization_write_authority_required"
            ),
        ):
            repos.task_repo.save(_candidate())

        with authorize_recovery_task_admin_write(
            task_id=source_id,
            source_task_id=source_id,
            goal_id=goal_id,
            action="delete",
            from_status="blocked_by_dependency",
            to_status="cancelled",
        ):
            with pytest.raises(
                ValueError,
                match=(
                    "recovery_source_finalization_write_authority_required"
                ),
            ):
                repos.task_repo.save(_candidate())

        assert (
            repos.task_repo.get_by_id(source_id).status
            == "blocked_by_dependency"
        )


def test_cancel_forwards_to_assigned_worker(monkeypatch):
    service = TaskAdminService()

    task = SimpleNamespace(id="task-1", status="todo", assigned_agent_url="http://worker-a:5000")
    monkeypatch.setattr(
        "agent.services.task_admin_service.get_repository_registry",
        lambda: SimpleNamespace(task_repo=SimpleNamespace(get_by_id=lambda _tid: task)),
    )

    calls = []

    def _cancel_task_requests(**values):
        calls.append(values)
        return {
            "attempted": True,
            "status": "ok",
            "worker_url": task.assigned_agent_url,
        }

    monkeypatch.setattr(
        "agent.services.request_cancellation_service.get_request_cancellation_service",
        lambda: SimpleNamespace(
            cancel_task_requests=_cancel_task_requests
        ),
    )
    def _update_status(_task_id, status, **_kwargs):
        task.status = status

    monkeypatch.setattr(
        "agent.services.task_admin_service.update_local_task_status",
        _update_status,
    )
    monkeypatch.setattr("agent.services.task_admin_service.log_audit", lambda *args, **kwargs: None)

    ok, msg, data = service.intervene_task(task_id="task-1", action="cancel", actor="test")
    assert ok is True
    assert msg == "ok"
    assert calls == [
        {"task_id": "task-1", "include_workers": True}
    ]
    forward = data.get("worker_cancel_forward") or {}
    assert forward.get("attempted") is True
    assert forward.get("status") == "ok"


def _vector_task_row(*, status: str = "todo") -> SimpleNamespace:
    task_id = "vector-index-" + ("a" * 32)
    scope = VectorIndexTrustedScope(
        workspace_id="workspace-a",
        repository_id="repo-a",
        profile_name="default",
        domain="codecompass",
    )
    return SimpleNamespace(
        id=task_id,
        status=status,
        task_kind="vector_index_operation",
        assigned_agent_url="http://worker-a:5000",
        source_task_id=None,
        goal_id=None,
        plan_id=None,
        derivation_reason=None,
        status_reason_details={},
        worker_execution_context={
            "vector_index_task": {
                "schema": "ananta.vector_index_task.v1",
                "job_id": task_id,
                "scope": scope.to_dict(),
                "scope_fingerprint": scope.fingerprint(),
            }
        },
    )


def _vector_workspace_authorization():
    return get_vector_store_authorization_policy().from_identity(
        {
            "sub": "admin-a",
            "role": "admin",
            "workspace_id": "workspace-a",
        }
    )


def test_vector_intervention_uses_dedicated_hub_lifecycle(
    monkeypatch,
) -> None:
    task = _vector_task_row()
    lifecycle_calls: list[dict] = []
    cancellation_calls: list[dict] = []
    monkeypatch.setattr(
        "agent.services.task_admin_service.get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: task
            )
        ),
    )
    monkeypatch.setattr(
        (
            "agent.services.vector_index_task_service."
            "get_vector_index_task_service"
        ),
        lambda: SimpleNamespace(
            cancel=lambda **kwargs: (
                lifecycle_calls.append(kwargs)
                or {"status": "cancelled"}
            )
        ),
    )
    monkeypatch.setattr(
        (
            "agent.services.request_cancellation_service."
            "get_request_cancellation_service"
        ),
        lambda: SimpleNamespace(
            cancel_task_requests=lambda **kwargs: (
                cancellation_calls.append(kwargs)
                or {"status": "ok"}
            )
        ),
    )

    ok, reason, data = TaskAdminService().intervene_task(
        task_id=task.id,
        action="cancel",
        actor="admin-a",
        vector_authorization=_vector_workspace_authorization(),
    )

    assert (ok, reason, data["status"]) == (
        True,
        "ok",
        "cancelled",
    )
    assert lifecycle_calls == [
        {"job_id": task.id, "actor": "admin-a"}
    ]
    assert cancellation_calls == [
        {"task_id": task.id, "include_workers": True}
    ]


def test_vector_intervention_fails_closed_for_generic_or_partial_domain(
    monkeypatch,
) -> None:
    task = _vector_task_row()
    monkeypatch.setattr(
        "agent.services.task_admin_service.get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: task
            )
        ),
    )

    ok, reason, data = TaskAdminService().intervene_task(
        task_id=task.id,
        action="pause",
        actor="admin-a",
        vector_authorization=_vector_workspace_authorization(),
    )
    assert ok is False
    assert reason == "vector_index_task_intervention_forbidden"
    assert data["http_status"] == 409

    task.worker_execution_context = {}
    ok, reason, data = TaskAdminService().intervene_task(
        task_id=task.id,
        action="cancel",
        actor="admin-a",
        vector_authorization=(
            get_vector_store_authorization_policy()
            .system_context(
                actor="run-control",
                purpose="run_control",
            )
        ),
    )
    assert ok is False
    assert reason == "vector_index_task_domain_binding_invalid"
    assert data["http_status"] == 409


def test_vector_intervention_direct_service_bypass_is_denied(
    monkeypatch,
) -> None:
    task = _vector_task_row()
    lifecycle_calls: list[dict] = []
    monkeypatch.setattr(
        "agent.services.task_admin_service.get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: task
            )
        ),
    )
    monkeypatch.setattr(
        (
            "agent.services.vector_index_task_service."
            "get_vector_index_task_service"
        ),
        lambda: SimpleNamespace(
            cancel=lambda **kwargs: lifecycle_calls.append(
                kwargs
            )
        ),
    )

    ok, reason, data = TaskAdminService().intervene_task(
        task_id=task.id,
        action="cancel",
        actor="forged-caller",
    )

    assert ok is False
    assert reason == "vector_store_admin_required"
    assert data == {
        "reason_code": "vector_store_admin_required",
        "http_status": 403,
    }
    assert lifecycle_calls == []


def test_vector_cleanup_direct_service_bypass_is_denied(
    monkeypatch,
) -> None:
    task = _vector_task_row()
    monkeypatch.setattr(
        "agent.services.task_admin_service.get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: task
            )
        ),
    )

    with pytest.raises(
        PermissionError,
        match="vector_store_admin_required",
    ):
        TaskAdminService().archive_task(task_id=task.id)


def test_intervention_reports_authoritative_commit_conflict(
    monkeypatch,
):
    service = TaskAdminService()
    task = SimpleNamespace(
        id="task-stale-retry",
        status="failed",
        assigned_agent_url=None,
        source_task_id=None,
        goal_id=None,
        plan_id=None,
        derivation_reason=None,
        status_reason_details={},
    )
    monkeypatch.setattr(
        "agent.services.task_admin_service.get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: task
            )
        ),
    )
    monkeypatch.setattr(
        "agent.services.task_admin_service.update_local_task_status",
        lambda *_args, **_kwargs: None,
    )

    ok, reason, data = service.intervene_task(
        task_id=task.id,
        action="retry",
        actor="test",
    )

    assert ok is False
    assert reason == "task_admin_status_commit_conflict"
    assert data["http_status"] == 409
    assert data["expected_status"] == "todo"
    assert data["current_status"] == "failed"


def test_active_recovery_child_archive_is_a_structured_conflict(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="archive-active",
                child_status="todo",
            )
        )

    response = client.post(
        f"/tasks/{child_id}/archive",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert (
        response.json["message"]
        == "recovery_child_cleanup_requires_closed_lineage"
    )
    conflict = response.json["data"]
    assert conflict["task_id"] == child_id
    assert conflict["source_task_id"] == source_id
    assert conflict["plan_id"] == plan_id
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repos = get_repository_registry()
        assert repos.task_repo.get_by_id(child_id) is not None
        assert repos.archived_task_repo.get_by_id(child_id) is None


def test_active_recovery_child_delete_cleanup_is_a_structured_conflict(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="delete-active",
                child_status="in_progress",
            )
        )

    response = client.post(
        "/tasks/cleanup",
        json={"mode": "delete", "task_ids": [child_id]},
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert (
        response.json["message"]
        == "recovery_child_cleanup_requires_closed_lineage"
    )
    errors = response.json["data"]["errors"]
    assert errors == [
        {
            "action": "delete",
            "error": (
                "recovery_child_cleanup_requires_closed_lineage"
            ),
            "http_status": 409,
            "id": child_id,
            "plan_id": plan_id,
            "reason_code": (
                "recovery_child_cleanup_requires_closed_lineage"
            ),
            "source_task_id": source_id,
            "task_id": child_id,
        }
    ]
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        assert (
            get_repository_registry().task_repo.get_by_id(child_id)
            is not None
        )


def test_bulk_cleanup_keeps_recovery_source_under_hub_control(
    client,
    app,
    admin_auth_header,
) -> None:
    with app.app_context():
        _goal_id, _plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="bulk-source-delete",
                child_status="assigned",
            )
        )

    response = client.post(
        "/tasks/cleanup",
        json={"mode": "delete", "task_ids": [source_id]},
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert response.json["message"] == (
        "recovery_source_cleanup_requires_hub_control"
    )
    assert response.json["data"]["deleted_ids"] == []
    error = response.json["data"]["errors"][0]
    assert error["id"] == source_id
    assert error["http_status"] == 409
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repos = get_repository_registry()
        assert repos.task_repo.get_by_id(source_id) is not None
        assert repos.task_repo.get_by_id(child_id) is not None


def test_active_recovery_child_cancel_cannot_bypass_hub_plan(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="cancel-active",
                child_status="assigned",
            )
        )

    response = client.post(
        f"/tasks/{child_id}/cancel",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert (
        response.json["message"]
        == "recovery_child_active_mutation_requires_hub_control"
    )
    assert response.json["data"]["source_task_id"] == source_id
    assert response.json["data"]["plan_id"] == plan_id
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        assert (
            get_repository_registry()
            .task_repo.get_by_id(child_id)
            .status
            == "assigned"
        )


def test_recovery_source_cancel_is_deterministic_409_without_mutation(
    client,
    app,
    admin_auth_header,
) -> None:
    with app.app_context():
        _goal_id, plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="cancel-source",
                child_status="assigned",
            )
        )
        direct = TaskAdminService().intervene_task(
            task_id=source_id,
            action="cancel",
            actor="test",
        )

    assert direct[0] is False
    assert direct[1] == (
        "recovery_source_cancel_requires_hub_control"
    )
    assert direct[2]["http_status"] == 409
    assert direct[2]["source_task_id"] == source_id
    assert direct[2]["plan_id"] == plan_id

    response = client.post(
        f"/tasks/{source_id}/cancel",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert response.json["message"] == (
        "recovery_source_cancel_requires_hub_control"
    )
    assert response.json["data"]["action"] == "cancel"
    assert response.json["data"]["source_task_id"] == source_id
    assert response.json["data"]["plan_id"] == plan_id
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repos = get_repository_registry()
        assert (
            repos.task_repo.get_by_id(source_id).status
            == "blocked_by_dependency"
        )
        assert (
            repos.task_repo.get_by_id(child_id).status
            == "assigned"
        )


def test_terminal_recovery_child_retry_cannot_resurrect_lineage(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="retry-terminal",
                child_status="failed",
            )
        )

    response = client.post(
        f"/tasks/{child_id}/retry",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert (
        response.json["message"]
        == "recovery_child_retry_requires_new_hub_plan"
    )
    assert response.json["data"]["source_task_id"] == source_id
    assert response.json["data"]["plan_id"] == plan_id
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        assert (
            get_repository_registry()
            .task_repo.get_by_id(child_id)
            .status
            == "failed"
        )


def test_closed_recovery_lineage_allows_terminal_child_archive(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, _plan_id, _source_id, child_id = (
            _persist_recovery_lineage(
                suffix="archive-closed",
                child_status="completed",
                source_status="completed",
                goal_status="completed",
            )
        )

    response = client.post(
        f"/tasks/{child_id}/archive",
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repos = get_repository_registry()
        assert repos.task_repo.get_by_id(child_id) is None
        assert repos.archived_task_repo.get_by_id(child_id) is not None


def test_inflight_recovery_result_fences_closed_lineage_cleanup(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, _plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="archive-inflight",
                child_status="completed",
                source_status="completed",
                goal_status="completed",
                lease={
                    "state": "worker_admitted",
                    "expires_at": time.time() + 300,
                },
            )
        )

    response = client.post(
        f"/tasks/{child_id}/archive",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert (
        response.json["message"]
        == "recovery_child_dispatch_inflight"
    )
    assert response.json["data"]["source_task_id"] == source_id
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        assert (
            get_repository_registry().task_repo.get_by_id(child_id)
            is not None
        )


def test_archive_rechecks_dispatch_lease_after_waiting_on_shared_fence(
    app,
    monkeypatch,
):
    """A lease committed after the first read must still stop cleanup."""

    from agent.services.lifecycle_service import (
        goal_mutation_lock_id,
    )
    from agent.services.repository_registry import (
        get_repository_registry,
    )
    from agent.services.task_mutation_lock_service import (
        get_task_mutation_lock_port,
    )

    with app.app_context():
        goal_id, plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="archive-race",
                child_status="completed",
                source_status="completed",
                goal_status="completed",
            )
        )
    real_lock_port = get_task_mutation_lock_port()
    lock_attempted = threading.Event()

    class _SignallingLockPort:
        @contextlib.contextmanager
        def mutation_locks(self, lock_ids):
            lock_attempted.set()
            with real_lock_port.mutation_locks(
                lock_ids
            ) as acquired:
                yield acquired

    monkeypatch.setattr(
        "agent.services.task_mutation_lock_service.get_task_mutation_lock_port",
        lambda: _SignallingLockPort(),
    )
    outcome: list[BaseException | bool] = []

    def _archive() -> None:
        try:
            outcome.append(
                TaskAdminService().archive_task(task_id=child_id)
            )
        except BaseException as exc:  # captured for the thread assertion
            outcome.append(exc)

    with real_lock_port.mutation_locks(
        {
            child_id,
            source_id,
            goal_mutation_lock_id(goal_id),
        }
    ) as acquired:
        assert acquired is True
        thread = threading.Thread(target=_archive)
        thread.start()
        assert lock_attempted.wait(timeout=2)

        repos = get_repository_registry()
        child = repos.task_repo.get_by_id(child_id)
        details = dict(child.status_reason_details or {})
        active_lease = _active_recovery_dispatch_lease(
            task_id=child_id,
            source_task_id=source_id,
            goal_id=goal_id,
            plan_id=plan_id,
        )
        details["recovery_dispatch_lease"] = active_lease
        child.status_reason_details = details
        child.updated_at = time.time()
        repos.task_repo.save(child)

        child = repos.task_repo.get_by_id(child_id)
        details = dict(child.status_reason_details or {})
        details["recovery_dispatch_lease"] = {
            **active_lease,
            "state": "worker_admitted",
            "admitted_at": time.time(),
            "admitted_worker_url": active_lease["worker_url"],
        }
        child.status_reason_details = details
        child.updated_at = time.time()
        repos.task_repo.save(child)

    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(
        outcome[0],
        RecoveryChildAdminMutationConflict,
    )
    assert outcome[0].reason_code == (
        "recovery_child_dispatch_inflight"
    )
    assert (
        get_repository_registry().task_repo.get_by_id(child_id)
        is not None
    )


def test_archived_recovery_child_restore_is_denied_single_and_bulk(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, _plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="restore-child",
                child_status="completed",
                source_status="completed",
                goal_status="completed",
            )
        )
    archived = client.post(
        f"/tasks/{child_id}/archive",
        headers=admin_auth_header,
    )
    assert archived.status_code == 200

    single = client.post(
        f"/tasks/archived/{child_id}/restore",
        headers=admin_auth_header,
    )
    assert single.status_code == 409
    assert single.json["message"] == (
        "recovery_lineage_restore_requires_hub_control"
    )
    assert single.json["data"]["source_task_id"] == source_id

    bulk = client.post(
        "/tasks/archived/restore/batch",
        json={"task_ids": [child_id]},
        headers=admin_auth_header,
    )
    assert bulk.status_code == 409
    assert bulk.json["message"] == (
        "recovery_lineage_restore_requires_hub_control"
    )
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repos = get_repository_registry()
        assert repos.task_repo.get_by_id(child_id) is None
        assert repos.archived_task_repo.get_by_id(child_id) is not None


def test_archived_recovery_source_restore_cannot_reopen_broken_dag(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="restore-source",
                child_status="in_progress",
            )
        )

    archived = client.post(
        f"/tasks/{source_id}/archive",
        headers=admin_auth_header,
    )
    assert archived.status_code == 200
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repos = get_repository_registry()
        assert repos.task_repo.get_by_id(source_id) is None
        assert repos.task_repo.get_by_id(child_id).status == "cancelled"

    restored = client.post(
        f"/tasks/archived/{source_id}/restore",
        headers=admin_auth_header,
    )
    assert restored.status_code == 409
    assert restored.json["message"] == (
        "recovery_lineage_restore_requires_hub_control"
    )
    assert restored.json["data"]["source_task_id"] == source_id
    assert restored.json["data"]["plan_id"] == plan_id
    with app.app_context():
        repos = get_repository_registry()
        assert repos.task_repo.get_by_id(source_id) is None
        assert repos.archived_task_repo.get_by_id(source_id) is not None


def test_recovery_source_retry_is_denied_without_false_success(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, plan_id, source_id, _child_id = (
            _persist_recovery_lineage(
                suffix="retry-source",
                child_status="cancelled",
                source_status="failed",
            )
        )

    response = client.post(
        f"/tasks/{source_id}/retry",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert response.json["message"] == (
        "recovery_source_retry_requires_new_hub_plan"
    )
    assert response.json["data"]["source_task_id"] == source_id
    assert response.json["data"]["plan_id"] == plan_id
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        assert (
            get_repository_registry()
            .task_repo.get_by_id(source_id)
            .status
            == "failed"
        )


def test_stopped_model_recovery_strategy_cannot_be_retried_generically(
    client,
    app,
    admin_auth_header,
):
    task_id = "source-admin-strategy-stop"
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        get_repository_registry().task_repo.save(
            TaskDB(
                id=task_id,
                status="waiting_for_review",
                status_reason_details={
                    "model_recovery_strategy": {
                        "schema": (
                            "ananta.model_recovery_strategy.v1"
                        ),
                        "status": "stopped",
                        "reason_code": (
                            "model_recovery_stop_selected"
                        ),
                        "recovery_actions": ["stop"],
                    }
                },
            )
        )

    response = client.post(
        f"/tasks/{task_id}/retry",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert response.json["message"] == (
        "recovery_source_retry_requires_new_hub_plan"
    )
    assert response.json["data"]["source_task_id"] == task_id
    assert response.json["data"]["plan_id"] is None
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        assert (
            get_repository_registry()
            .task_repo.get_by_id(task_id)
            .status
            == "waiting_for_review"
        )


def test_repository_preserves_model_recovery_strategy_metadata(app):
    task_id = "source-admin-strategy-merge"
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repo = get_repository_registry().task_repo
        persisted = repo.save(
            TaskDB(
                id=task_id,
                status="waiting_for_review",
                status_reason_details={
                    "model_recovery_strategy": {
                        "schema": (
                            "ananta.model_recovery_strategy.v1"
                        ),
                        "status": "stopped",
                        "reason_code": (
                            "model_recovery_stop_selected"
                        ),
                    }
                },
            )
        )
        persisted.status_reason_details = {}
        persisted.updated_at = time.time() + 1

        repo.save(persisted)

        strategy = repo.get_by_id(
            task_id
        ).status_reason_details["model_recovery_strategy"]
        assert strategy["status"] == "stopped"
        assert strategy["reason_code"] == (
            "model_recovery_stop_selected"
        )


def test_direct_archived_recovery_child_delete_rechecks_active_lineage(
    client,
    app,
    admin_auth_header,
):
    from agent.db_models import ArchivedTaskDB

    with app.app_context():
        _goal_id, plan_id, source_id, child_id = (
            _persist_recovery_lineage(
                suffix="direct-delete-child",
                child_status="cancelled",
            )
        )
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        repos = get_repository_registry()
        child = repos.task_repo.get_by_id(child_id)
        repos.archived_task_repo.save(
            ArchivedTaskDB(**child.model_dump())
        )
        repos.task_repo.delete(child_id)

    response = client.delete(
        f"/tasks/archived/{child_id}",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert response.json["message"] == (
        "recovery_child_cleanup_requires_closed_lineage"
    )
    assert response.json["data"]["source_task_id"] == source_id
    assert response.json["data"]["plan_id"] == plan_id
    with app.app_context():
        assert (
            get_repository_registry()
            .archived_task_repo.get_by_id(child_id)
            is not None
        )


def test_direct_archived_recovery_source_delete_requires_hub_control(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, _plan_id, source_id, _child_id = (
            _persist_recovery_lineage(
                suffix="direct-delete-source",
                child_status="todo",
            )
        )
    archived = client.post(
        f"/tasks/{source_id}/archive",
        headers=admin_auth_header,
    )
    assert archived.status_code == 200

    response = client.delete(
        f"/tasks/archived/{source_id}",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    assert response.json["message"] == (
        "recovery_source_cleanup_requires_hub_control"
    )
    assert response.json["data"]["source_task_id"] == source_id
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        assert (
            get_repository_registry()
            .archived_task_repo.get_by_id(source_id)
            is not None
        )


def test_api_retention_preserves_archived_recovery_lineage(
    client,
    app,
    admin_auth_header,
):
    with app.app_context():
        _goal_id, _plan_id, source_id, _child_id = (
            _persist_recovery_lineage(
                suffix="api-retention",
                child_status="todo",
            )
        )
    archived = client.post(
        f"/tasks/{source_id}/archive",
        headers=admin_auth_header,
    )
    assert archived.status_code == 200

    response = client.post(
        "/tasks/archive/retention/apply",
        json={"retain_seconds": 0.000001},
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    errors = response.json["data"]["errors"]
    source_error = next(
        item for item in errors if item["id"] == source_id
    )
    assert source_error["reason_code"] == (
        "recovery_lineage_retention_preserved"
    )
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        assert (
            get_repository_registry()
            .archived_task_repo.get_by_id(source_id)
            is not None
        )
