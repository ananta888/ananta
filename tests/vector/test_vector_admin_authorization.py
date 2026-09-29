from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent.auth import generate_token
from agent.config import settings
from agent.services.knowledge_index_task_ingress_policy import (
    BOUND_KNOWLEDGE_INDEX_MUTATION_REASON,
    RESERVED_KNOWLEDGE_INDEX_TASK_INGRESS_REASON,
)
from agent.services.task_admin_service import TaskAdminService
from agent.services.task_claim_service import TaskClaimService
from agent.services.task_management_service import (
    TaskManagementService,
)
from agent.services.task_orchestration_service import (
    TaskOrchestrationDependencies,
    TaskOrchestrationService,
)
from agent.services.task_runtime_service import (
    get_local_task_status,
    update_local_task_status,
)
from agent.services.vector_index_task_contracts import (
    VectorIndexTrustedScope,
)
from agent.services.vector_index_task_ingress_policy import (
    RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON,
)
from agent.services.vector_store_authorization_policy import (
    get_vector_store_authorization_policy,
    reserved_vector_index_marker,
)
from agent.services.vector_task_admin_guard_service import (
    require_authoritative_vector_task,
)


class _TaskRow(SimpleNamespace):
    def model_dump(self) -> dict:
        return dict(vars(self))


def _vector_context(
    workspace_id: str = "workspace-a",
    task_id: str = "vector-index-" + ("a" * 32),
) -> dict:
    scope = VectorIndexTrustedScope(
        workspace_id=workspace_id,
        repository_id="repo-a",
        profile_name="default",
        domain="codecompass",
    )
    return {
        "vector_index_task": {
            "schema": "ananta.vector_index_task.v1",
            "job_id": task_id,
            "scope": scope.to_dict(),
            "scope_fingerprint": scope.fingerprint(),
        }
    }


def _vector_task(
    *,
    workspace_id: str = "workspace-a",
    task_id: str = "vector-index-" + ("a" * 32),
    status: str = "failed",
) -> _TaskRow:
    return _TaskRow(
        id=task_id,
        status=status,
        task_kind="vector_index_operation",
        required_capabilities=[
            "retrieval",
            "index_write",
            "vector_index_operation",
        ],
        assigned_agent_url="http://worker-a:5000",
        assigned_agent_token=None,
        source_task_id=None,
        goal_id=None,
        plan_id=None,
        derivation_reason=None,
        status_reason_details={},
        worker_execution_context=_vector_context(
            workspace_id,
            task_id,
        ),
    )


def _authorization(workspace_id: str):
    return get_vector_store_authorization_policy().from_identity(
        {
            "sub": "workspace-admin",
            "role": "admin",
            "workspace_id": workspace_id,
        },
        authenticated_admin=True,
    )


def _headers(workspace_id: str) -> dict[str, str]:
    token = generate_token(
        {
            "sub": "workspace-admin",
            "role": "admin",
            "workspace_id": workspace_id,
        },
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def _system_headers() -> dict[str, str]:
    token = generate_token(
        {
            "sub": "system-operator",
            "role": "admin",
            "roles": ["system_admin"],
        },
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def test_user_admin_is_never_promoted_to_global_vector_admin() -> None:
    policy = get_vector_store_authorization_policy()
    authorization = _authorization("workspace-a")

    assert authorization.roles == frozenset({"admin"})
    policy.require_task_admin(
        authorization,
        _vector_task(workspace_id="workspace-a"),
    )
    with pytest.raises(
        PermissionError,
        match="vector_store_workspace_forbidden",
    ):
        policy.require_task_admin(
            authorization,
            _vector_task(workspace_id="workspace-b"),
        )
    with pytest.raises(
        PermissionError,
        match="vector_store_global_admin_required",
    ):
        policy.require_global_admin(authorization)


def test_auth_disabled_or_anonymous_context_never_gets_system_authority(
) -> None:
    policy = get_vector_store_authorization_policy()
    authorization = policy.from_identity(
        {"auth_mode": "auth_disabled"},
        authenticated_admin=True,
    )
    anonymous = policy.from_identity(
        None,
        authenticated_admin=True,
    )

    assert authorization.roles == frozenset()
    assert anonymous.roles == frozenset()
    for candidate in (authorization, anonymous):
        with pytest.raises(
            PermissionError,
            match="vector_store_admin_required",
        ):
            policy.require_task_admin(
                candidate,
                _vector_task(),
            )


def test_internal_vector_authority_requires_an_explicit_known_purpose() -> None:
    policy = get_vector_store_authorization_policy()
    authorization = policy.system_context(
        actor="goal-purge",
        purpose="goal_purge",
    )

    assert authorization.system_purpose == "goal_purge"
    policy.require_task_admin(
        authorization,
        _vector_task(workspace_id="workspace-b"),
    )
    with pytest.raises(
        ValueError,
        match="vector_store_system_authorization_purpose_invalid",
    ):
        policy.system_context(
            actor="generic-internal-caller",
            purpose="cleanup",
        )
    with pytest.raises(
        ValueError,
        match="vector_store_system_authorization_actor_invalid",
    ):
        policy.system_context(actor="", purpose="run_control")


@pytest.mark.parametrize(
    "auth_mode",
    ["agent_static_token", "agent_jwt"],
)
def test_authenticated_service_identity_gets_explicit_global_authority(
    auth_mode,
) -> None:
    policy = get_vector_store_authorization_policy()
    authorization = policy.from_identity(
        {
            "sub": "hub-service",
            "role": "worker",
            "auth_mode": auth_mode,
        },
        authenticated_admin=True,
    )

    assert "system_admin" in authorization.roles
    policy.require_global_admin(authorization)


def test_schema_only_vector_envelope_is_not_an_authoritative_binding(
) -> None:
    partial = _vector_task()
    partial.worker_execution_context = {
        "vector_index_task": {
            "schema": "ananta.vector_index_task.v1"
        }
    }

    with pytest.raises(
        ValueError,
        match="vector_index_task_domain_binding_invalid",
    ):
        require_authoritative_vector_task(partial)


@pytest.mark.parametrize(
    ("task", "marker"),
    [
        ({"source": "vector_index"}, "source"),
        (
            {
                "history": [
                    {
                        "event_type": "task_ingested",
                        "details": {"source": "vector_index"},
                    }
                ]
            },
            "source",
        ),
        (
            {"task_kind": "vector_index_operation"},
            "task_kind",
        ),
        (
            {
                "worker_execution_context": {
                    "vector_index_task": {}
                }
            },
            "worker_execution_context.vector_index_task",
        ),
    ],
)
def test_reserved_vector_marker_covers_source_history_and_partial_rows(
    task,
    marker,
) -> None:
    assert reserved_vector_index_marker(task) == marker


def test_generic_cancel_retry_and_kill_use_the_same_workspace_policy(
    client,
    app,
    monkeypatch,
) -> None:
    task_id = "vector-index-" + ("b" * 32)
    with app.app_context():
        update_local_task_status(
            task_id,
            "failed",
            task_kind="vector_index_operation",
            required_capabilities=[
                "retrieval",
                "index_write",
                "vector_index_operation",
            ],
            worker_execution_context=_vector_context(
                "workspace-a",
                task_id,
            ),
            force=True,
        )

    lifecycle_calls: list[tuple[str, dict]] = []
    cancellation_calls: list[dict] = []
    lifecycle = SimpleNamespace(
        cancel=lambda **kwargs: (
            lifecycle_calls.append(("cancel", kwargs))
            or {"status": "cancelled"}
        ),
        retry=lambda **kwargs: (
            lifecycle_calls.append(("retry", kwargs))
            or {"status": "queued"}
        ),
    )
    monkeypatch.setattr(
        (
            "agent.services.vector_index_task_service."
            "get_vector_index_task_service"
        ),
        lambda: lifecycle,
    )
    cancellation = SimpleNamespace(
        cancel_task_requests=lambda **kwargs: (
            cancellation_calls.append(kwargs)
            or {"status": "ok"}
        )
    )
    monkeypatch.setattr(
        (
            "agent.services.request_cancellation_service."
            "get_request_cancellation_service"
        ),
        lambda: cancellation,
    )
    monkeypatch.setattr(
        (
            "agent.routes.tasks.management."
            "get_request_cancellation_service"
        ),
        lambda: cancellation,
    )

    for suffix in ("cancel", "retry", "kill-requests"):
        response = client.post(
            f"/tasks/{task_id}/{suffix}",
            headers=_headers("workspace-b"),
        )
        assert response.status_code == 403
        assert response.get_json()["message"] == (
            "vector_store_workspace_forbidden"
        )
    assert lifecycle_calls == []
    assert cancellation_calls == []

    cancelled = client.post(
        f"/tasks/{task_id}/cancel",
        headers=_headers("workspace-a"),
    )
    retried = client.post(
        f"/tasks/{task_id}/retry",
        headers=_headers("workspace-a"),
    )
    killed = client.post(
        f"/tasks/{task_id}/kill-requests",
        headers=_headers("workspace-a"),
    )

    assert cancelled.status_code == 200
    assert retried.status_code == 200
    assert killed.status_code == 200
    assert [name for name, _ in lifecycle_calls] == [
        "cancel",
        "retry",
    ]
    assert all(
        call["actor"] == "workspace-admin"
        for _, call in lifecycle_calls
    )
    assert len(cancellation_calls) == 2


@pytest.mark.parametrize(
    "reserved_payload",
    [
        {"source": "vector_index"},
        {
            "history": [
                {
                    "event_type": "task_ingested",
                    "details": {"source": "vector_index"},
                }
            ]
        },
        {"task_kind": "vector_index_operation"},
        {
            "worker_execution_context": {
                "vector_index_task": {}
            }
        },
    ],
)
def test_control_center_create_rejects_all_reserved_vector_markers(
    client,
    admin_auth_header,
    reserved_payload,
) -> None:
    response = client.post(
        "/api/tasks",
        headers=admin_auth_header,
        json={
            "title": "forged Vector task",
            **reserved_payload,
        },
    )

    assert response.status_code == 403
    body = response.get_json()
    assert body["message"] == (
        RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON
    )


@pytest.mark.parametrize(
    "reserved_payload",
    [
        {"source": "knowledge_index"},
        {"task_kind": "codecompass_index_build"},
        {
            "worker_execution_context": {
                "knowledge_index_job": {}
            }
        },
    ],
)
def test_control_center_create_rejects_reserved_knowledge_index_markers(
    client,
    admin_auth_header,
    reserved_payload,
) -> None:
    response = client.post(
        "/api/tasks",
        headers=admin_auth_header,
        json={
            "title": "forged knowledge-index task",
            **reserved_payload,
        },
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == (
        RESERVED_KNOWLEDGE_INDEX_TASK_INGRESS_REASON
    )


def test_control_center_patch_rejects_bound_knowledge_index_task(
    client,
    app,
    admin_auth_header,
) -> None:
    task_id = "bound-knowledge-index-control-center"
    with app.app_context():
        update_local_task_status(
            task_id,
            "assigned",
            task_kind="codecompass_index_build",
            worker_execution_context={
                "knowledge_index_job": {
                    "schema": (
                        "ananta.knowledge_index_execution_job.v2"
                    ),
                    "job_id": task_id,
                }
            },
            force=True,
        )

    response = client.patch(
        f"/api/tasks/{task_id}",
        headers=admin_auth_header,
        json={"status": "completed"},
    )

    assert response.status_code == 409
    body = response.get_json()
    assert body["message"] == BOUND_KNOWLEDGE_INDEX_MUTATION_REASON
    assert body["data"]["reason_code"] == (
        BOUND_KNOWLEDGE_INDEX_MUTATION_REASON
    )
    assert body["data"]["task_id"] == task_id
    assert body["data"]["action"] == "control_center_patch"
    with app.app_context():
        assert get_local_task_status(task_id)["status"] == "assigned"


def test_control_center_patch_rejects_existing_source_history_marker(
    client,
    app,
    admin_auth_header,
) -> None:
    task_id = "vector-source-history-control-center"
    with app.app_context():
        update_local_task_status(
            task_id,
            "todo",
            event_type="task_ingested",
            event_details={"source": "vector_index"},
            force=True,
        )

    response = client.patch(
        f"/api/tasks/{task_id}",
        headers=admin_auth_header,
        json={"title": "generic overwrite attempt"},
    )

    assert response.status_code == 403
    body = response.get_json()
    assert body["message"] == (
        RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON
    )
    assert body["data"]["reserved_field"] == "source"


def test_control_center_session_rejects_partial_vector_without_side_effects(
    client,
    app,
    admin_auth_header,
    monkeypatch,
) -> None:
    task_id = "vector-partial-control-center-session"
    with app.app_context():
        update_local_task_status(
            task_id,
            "todo",
            task_kind="vector_index_operation",
            worker_execution_context={
                "vector_index_task": {
                    "schema": "ananta.vector_index_task.v1",
                }
            },
            force=True,
        )

    share_session_calls: list[dict] = []
    monkeypatch.setattr(
        (
            "agent.routes.control_center_api."
            "get_share_session_service"
        ),
        lambda: SimpleNamespace(
            create_session=lambda **kwargs: (
                share_session_calls.append(kwargs)
                or {"id": "unexpected"}
            )
        ),
    )

    response = client.post(
        f"/api/tasks/{task_id}/sessions",
        headers=admin_auth_header,
        json={"title": "generic session bypass"},
    )

    assert response.status_code == 403
    body = response.get_json()
    assert body["message"] == (
        RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON
    )
    assert body["data"]["reserved_field"] == "task_kind"
    assert share_session_calls == []
    with app.app_context():
        from agent.services.repository_registry import (
            get_repository_registry,
        )

        assert (
            get_repository_registry()
            .agent_session_repo.get_by_task_id(task_id)
            == []
        )


def test_derivation_backfill_skips_partial_vector_rows(
    client,
    app,
    admin_auth_header,
) -> None:
    parent_id = "vector-backfill-parent"
    task_id = "vector-backfill-partial"
    with app.app_context():
        update_local_task_status(parent_id, "todo", force=True)
        update_local_task_status(
            task_id,
            "todo",
            parent_task_id=parent_id,
            event_type="task_ingested",
            event_details={"source": "vector_index"},
            force=True,
        )

    response = client.post(
        "/tasks/derivation/backfill",
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert task_id not in data["updated_ids"]
    assert task_id in data["skipped_reserved_ids"]
    assert data["skipped_reserved_count"] >= 1
    with app.app_context():
        task = get_local_task_status(task_id)
    assert task is not None
    assert task.get("source_task_id") is None


def test_claim_rechecks_the_authoritative_row_for_vector_domain_races(
    monkeypatch,
) -> None:
    initial = _TaskRow(
        id="ordinary-at-first-read",
        task_kind="coding",
        worker_execution_context={},
    )
    vector_payload = _vector_task().model_dump()
    claim_calls: list[dict] = []

    class Queue:
        @staticmethod
        def claim_task(**kwargs):
            claim_calls.append(kwargs)
            allowed, _reason = kwargs["claim_validator"](
                vector_payload
            )
            assert allowed is False
            return False

    class Policy:
        @staticmethod
        def validate_lease_duration(value):
            return value

        @staticmethod
        def can_claim_task(*_args, **_kwargs):
            raise AssertionError(
                "Vector tasks must be denied before generic claim policy"
            )

    monkeypatch.setattr(
        "agent.services.task_claim_service.get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: initial
            )
        ),
    )

    result = TaskClaimService().claim_task(
        task_id=initial.id,
        agent_url="http://worker-a:5000",
        requested_lease=120,
        idempotency_key="claim-race",
        policy=Policy(),
        task_queue_service=Queue(),
    )

    assert result["code"] == 403
    assert result["error"] == (
        RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON
    )
    assert len(claim_calls) == 1


def test_orchestration_complete_and_delegate_deny_source_only_vector_rows(
) -> None:
    vector_history_task = {
        "id": "vector-history-task",
        "status": "todo",
        "history": [
            {
                "event_type": "task_ingested",
                "details": {"source": "vector_index"},
            }
        ],
    }

    def forbidden_side_effect(*_args, **_kwargs):
        raise AssertionError("generic orchestration must not mutate")

    service = TaskOrchestrationService(
        TaskOrchestrationDependencies(
            get_task_status=lambda _task_id: vector_history_task,
            update_task_status=forbidden_side_effect,
            forward_task_to_worker=forbidden_side_effect,
            repository_registry=forbidden_side_effect,
            routing_advisor=forbidden_side_effect,
            context_policy_service=forbidden_side_effect,
            execution_tracking_service=forbidden_side_effect,
        )
    )

    completed = service.complete_task(
        task_id=vector_history_task["id"],
        payload={},
        verification_service=SimpleNamespace(),
        worker_job_service=SimpleNamespace(),
        result_memory_service=SimpleNamespace(),
    )
    delegated = service.delegate_task(
        task_id=vector_history_task["id"],
        data=SimpleNamespace(),
        worker_job_service=SimpleNamespace(),
        worker_contract_service=SimpleNamespace(),
        agent_registry_service=SimpleNamespace(),
        result_memory_service=SimpleNamespace(),
        verification_service=SimpleNamespace(),
    )

    assert completed["code"] == 403
    assert delegated["code"] == 403
    assert completed["error"] == delegated["error"] == (
        RESERVED_VECTOR_INDEX_TASK_INGRESS_REASON
    )


def test_run_control_passes_explicit_system_vector_authority(
    monkeypatch,
) -> None:
    from agent.services.run_control_service import RunControlService

    calls: list[dict] = []
    monkeypatch.setattr(
        "agent.services.service_registry.get_core_services",
        lambda: SimpleNamespace(
            task_admin_service=SimpleNamespace(
                intervene_task=lambda **kwargs: (
                    calls.append(kwargs)
                    or (
                        True,
                        "ok",
                        {"id": kwargs["task_id"], "status": "cancelled"},
                    )
                )
            )
        ),
    )
    command = SimpleNamespace(
        task_id="vector-run-control",
        requested_by="operator-a",
        status="accepted",
        result={},
        effective_at=None,
    )

    RunControlService()._task_intervene(command, "cancel")

    assert command.status == "applied"
    assert len(calls) == 1
    authorization = calls[0]["vector_authorization"]
    assert authorization.source == "internal_control_plane"
    assert authorization.system_purpose == "run_control"
    assert authorization.actor == "operator-a"


def test_assignment_auto_assignment_and_unassignment_require_scope(
    monkeypatch,
) -> None:
    task = _vector_task().model_dump()
    updates: list[dict] = []
    monkeypatch.setattr(
        "agent.services.task_management_service.get_local_task_status",
        lambda _task_id: task,
    )
    monkeypatch.setattr(
        "agent.services.task_management_service.update_local_task_status",
        lambda *_args, **kwargs: updates.append(kwargs),
    )

    service = TaskManagementService()
    assignment = SimpleNamespace(
        agent_url="http://worker-b:5000",
        token=None,
        task_kind=None,
        required_capabilities=[],
    )
    forbidden = _authorization("workspace-b")
    allowed = _authorization("workspace-a")

    assert service.assign_task(
        task_id=task["id"],
        data=assignment,
        vector_authorization=forbidden,
    )["code"] == 403
    assert service.auto_assign_task(
        task_id=task["id"],
        payload={},
        agent_registry_service=SimpleNamespace(),
        worker_contract_service=SimpleNamespace(),
        vector_authorization=forbidden,
    )["code"] == 403
    assert service.unassign_task(
        task_id=task["id"],
        vector_authorization=forbidden,
    )["code"] == 403
    assert updates == []

    override = SimpleNamespace(
        agent_url="http://worker-b:5000",
        token="forged-worker-token",
        task_kind="coding",
        required_capabilities=["shell.execute"],
    )
    assert service.assign_task(
        task_id=task["id"],
        data=override,
        vector_authorization=allowed,
    )["error"] == (
        "vector_index_task_assignment_override_forbidden"
    )
    assert service.auto_assign_task(
        task_id=task["id"],
        payload={"task_kind": "coding"},
        agent_registry_service=SimpleNamespace(),
        worker_contract_service=SimpleNamespace(),
        vector_authorization=allowed,
    )["error"] == (
        "vector_index_task_assignment_override_forbidden"
    )

    unassigned = service.unassign_task(
        task_id=task["id"],
        vector_authorization=allowed,
    )
    assert unassigned["data"]["unassigned"] is True
    assert len(updates) == 1


def test_proposal_review_requires_scope_then_stays_fail_closed(
    monkeypatch,
) -> None:
    task = _vector_task().model_dump()
    monkeypatch.setattr(
        "agent.services.task_management_service.get_local_task_status",
        lambda _task_id: task,
    )
    service = TaskManagementService()

    forbidden = service.review_task_proposal(
        task_id=task["id"],
        action="approve",
        comment=None,
        vector_authorization=_authorization("workspace-b"),
    )
    allowed = service.review_task_proposal(
        task_id=task["id"],
        action="approve",
        comment=None,
        vector_authorization=_authorization("workspace-a"),
    )

    assert forbidden["code"] == 403
    assert forbidden["error"] == "vector_store_workspace_forbidden"
    assert allowed["code"] == 409
    assert allowed["error"] == (
        "vector_index_task_intervention_forbidden"
    )


def test_archived_delete_and_cleanup_require_scope_and_allow_owner(
    monkeypatch,
) -> None:
    task = _vector_task(status="cancelled")
    deleted: list[str] = []

    class ArchivedRepository:
        @staticmethod
        def get_by_id(_task_id):
            return task

        @staticmethod
        def get_all(*_args, **_kwargs):
            return [task]

        @staticmethod
        def delete(task_id):
            deleted.append(task_id)

    repos = SimpleNamespace(
        archived_task_repo=ArchivedRepository(),
        task_repo=SimpleNamespace(get_by_id=lambda _task_id: None),
    )
    monkeypatch.setattr(
        "agent.services.task_admin_service.get_repository_registry",
        lambda: repos,
    )

    @contextmanager
    def mutation_locks(_lock_ids):
        yield True

    monkeypatch.setattr(
        (
            "agent.services.task_mutation_lock_service."
            "get_task_mutation_lock_port"
        ),
        lambda: SimpleNamespace(mutation_locks=mutation_locks),
    )
    monkeypatch.setattr(
        (
            "agent.services.recovery_dispatch_gate_service."
            "get_recovery_dispatch_gate_service"
        ),
        lambda: SimpleNamespace(
            is_recovery_child=lambda _task: False
        ),
    )
    service = TaskAdminService()

    with pytest.raises(
        PermissionError,
        match="vector_store_workspace_forbidden",
    ):
        service.delete_archived_task(
            task_id=task.id,
            vector_authorization=_authorization("workspace-b"),
        )
    assert deleted == []

    denied_ids, errors = service.cleanup_archived_tasks(
        statuses=set(),
        team_id="",
        before_ts=None,
        task_ids={task.id},
        vector_authorization=_authorization("workspace-b"),
    )
    assert denied_ids == []
    assert errors[0]["http_status"] == 403
    assert deleted == []

    assert service.delete_archived_task(
        task_id=task.id,
        vector_authorization=_authorization("workspace-a"),
    )
    assert deleted == [task.id]


def test_goal_purge_keeps_every_row_when_partial_vector_cancel_fails(
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    from agent.db_models import GoalDB, TaskDB
    from agent.repository import goal_repo, task_repo

    goal = goal_repo.save(
        GoalDB(
            goal="Vector purge CAS conflict",
            summary="must remain intact",
            status="running",
            source="test",
            requested_by="admin",
        )
    )
    task = task_repo.save(
        TaskDB(
            id="vector-purge-cas-conflict",
            title="running Vector task",
            status="running",
            goal_id=goal.id,
            goal_trace_id=goal.trace_id,
            # Deliberately partial: the reserved kind alone must
            # still select the fail-closed purge boundary.
            task_kind="vector_index_operation",
        )
    )
    interventions: list[dict] = []
    prompt_trace_calls: list[str] = []

    def fail_cancel(**kwargs):
        interventions.append(kwargs)
        return (
            False,
            "vector_index_task_cas_conflict",
            {
                "reason_code": "vector_index_task_cas_conflict",
                "http_status": 409,
            },
        )

    monkeypatch.setattr(
        "agent.services.goal_purge_service.get_task_admin_service",
        lambda: SimpleNamespace(intervene_task=fail_cancel),
    )
    monkeypatch.setattr(
        "agent.services.goal_purge_service.get_prompt_trace_service",
        lambda: SimpleNamespace(
            delete_by_goal_id=lambda goal_id: (
                prompt_trace_calls.append(goal_id) or 0
            )
        ),
    )

    response = client.delete(
        f"/goals/{goal.id}/purge",
        headers=admin_auth_header,
    )

    assert response.status_code == 409
    body = response.get_json()
    assert body["message"] == (
        "vector_index_goal_purge_cancel_required"
    )
    assert body["data"]["lifecycle_reason"] == (
        "vector_index_task_cas_conflict"
    )
    assert goal_repo.get_by_id(goal.id) is not None
    persisted_task = task_repo.get_by_id(task.id)
    assert persisted_task is not None
    assert persisted_task.status == "running"
    assert prompt_trace_calls == []
    authorization = interventions[0]["vector_authorization"]
    assert authorization.system_purpose == "goal_purge"
    assert authorization.actor == "goal_purge"


def test_auth_disabled_cannot_trigger_goal_purge_system_authority(
    client,
    app,
    monkeypatch,
) -> None:
    from agent.db_models import GoalDB
    from agent.repository import goal_repo

    goal = goal_repo.save(
        GoalDB(
            goal="anonymous purge must be denied",
            summary="auth-disabled is not a system identity",
            status="running",
            source="test",
            requested_by="admin",
        )
    )
    monkeypatch.setitem(app.config, "AGENT_TOKEN", "")
    monkeypatch.delenv("AGENT_TOKEN", raising=False)

    response = client.delete(f"/goals/{goal.id}/purge")

    assert response.status_code == 401
    assert goal_repo.get_by_id(goal.id) is not None


@pytest.mark.parametrize(
    "path",
    [
        "/goals/goal-a/kill-requests",
        "/internal/goals/goal-a/kill-requests",
        "/goals/kill-all-requests",
        "/internal/goals/kill-all-requests",
        "/internal/tasks/task-a/kill-requests",
    ],
)
def test_auth_disabled_cannot_trigger_request_kill_boundaries(
    client,
    app,
    monkeypatch,
    path,
) -> None:
    monkeypatch.setitem(app.config, "AGENT_TOKEN", "")
    monkeypatch.delenv("AGENT_TOKEN", raising=False)

    response = client.post(path)

    assert response.status_code == 401


def test_goal_and_global_request_kill_enforce_vector_scope(
    client,
    app,
    monkeypatch,
) -> None:
    goal_id = "vector-request-kill-goal"
    task_id = "vector-index-" + ("e" * 32)
    with app.app_context():
        update_local_task_status(
            task_id,
            "running",
            goal_id=goal_id,
            task_kind="vector_index_operation",
            required_capabilities=[
                "retrieval",
                "index_write",
                "vector_index_operation",
            ],
            worker_execution_context=_vector_context(
                "workspace-a",
                task_id,
            ),
            force=True,
        )

    calls: list[tuple[str, dict]] = []
    cancellation = SimpleNamespace(
        cancel_goal_requests=lambda **kwargs: (
            calls.append(("goal", kwargs))
            or {"status": "ok"}
        ),
        cancel_all_requests=lambda **kwargs: (
            calls.append(("all", kwargs))
            or {"status": "ok"}
        ),
    )
    monkeypatch.setattr(
        (
            "agent.services.request_cancellation_service."
            "get_request_cancellation_service"
        ),
        lambda: cancellation,
    )

    cross_scope = client.post(
        f"/goals/{goal_id}/kill-requests",
        headers=_headers("workspace-b"),
    )
    same_scope = client.post(
        f"/goals/{goal_id}/kill-requests",
        headers=_headers("workspace-a"),
    )
    non_global = client.post(
        "/goals/kill-all-requests",
        headers=_headers("workspace-a"),
    )
    global_admin = client.post(
        "/goals/kill-all-requests",
        headers=_system_headers(),
    )

    assert cross_scope.status_code == 403
    assert same_scope.status_code == 200
    assert non_global.status_code == 403
    assert global_admin.status_code == 200
    assert [kind for kind, _ in calls] == ["goal", "all"]


def test_goal_purge_keeps_ordinary_task_compatibility_on_cancel_failure(
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    from agent.db_models import GoalDB, TaskDB
    from agent.repository import goal_repo, task_repo

    goal = goal_repo.save(
        GoalDB(
            goal="ordinary purge compatibility",
            summary="legacy task cleanup",
            status="running",
            source="test",
            requested_by="admin",
        )
    )
    task = task_repo.save(
        TaskDB(
            id="ordinary-purge-cancel-failure",
            title="ordinary task",
            status="running",
            goal_id=goal.id,
            goal_trace_id=goal.trace_id,
            task_kind="coding",
        )
    )
    monkeypatch.setattr(
        "agent.services.goal_purge_service.get_task_admin_service",
        lambda: SimpleNamespace(
            intervene_task=lambda **_kwargs: (
                False,
                "invalid_transition",
                {"http_status": 409},
            )
        ),
    )
    monkeypatch.setattr(
        "agent.services.goal_purge_service.get_prompt_trace_service",
        lambda: SimpleNamespace(
            delete_by_goal_id=lambda _goal_id: 0
        ),
    )

    response = client.delete(
        f"/goals/{goal.id}/purge",
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    assert goal_repo.get_by_id(goal.id) is None
    assert task_repo.get_by_id(task.id) is None
