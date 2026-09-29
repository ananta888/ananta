import json
from types import SimpleNamespace

import pytest

from agent.services.source_control_access_policy import HubSourcePrincipal
from agent.services.task_query_service import (
    _TASK_READ_SCAN_CHUNK_SIZE,
    _TASK_READ_SCAN_MAX_ROWS,
    TaskQueryService,
)
from agent.services.task_read_access_service import (
    TaskReadAccessContext,
    TaskReadAccessError,
    TaskReadAccessService,
)
from agent.services.task_read_projection_service import (
    TaskReadProjectionService,
)
from tests.project_access_fakes import AllowProjectAccess


class _ProjectAccess:
    def __init__(self, *, role: str = "viewer") -> None:
        self.role = role
        self.calls: list[dict] = []

    def require(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(role=self.role)


class _OrganizationMembership:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    def can_view(self, **_kwargs) -> bool:
        return self.allowed


class _TaskRow:
    def __init__(self, payload: dict) -> None:
        self._payload = dict(payload)

    def model_dump(self) -> dict:
        return dict(self._payload)


class _PagedTaskRepository:
    def __init__(self, rows: list[_TaskRow]) -> None:
        self.rows = list(rows)
        self.calls: list[dict] = []

    def get_paged(self, *, limit: int, offset: int, **filters):
        self.calls.append(
            {"limit": limit, "offset": offset, **filters}
        )
        return self.rows[offset : offset + limit]

    def get_all(self, *, limit: int, offset: int, **filters):
        self.calls.append(
            {"limit": limit, "offset": offset, **filters}
        )
        return self.rows[offset : offset + limit]


def _principal(
    *,
    subject_id: str = "user-a",
    tenant_id: str | None = "tenant-a",
    project_id: str | None = "project-a",
    roles: frozenset[str] = frozenset(),
) -> HubSourcePrincipal:
    return HubSourcePrincipal(
        subject_id=subject_id,
        tenant_id=tenant_id,
        project_id=project_id,
        roles=roles,
    )


def _scoped_task(**overrides) -> dict:
    task = {
        "id": "task-a",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "organization_id": "organization-a",
        "history": [
            {
                "event_type": "task_ingested",
                "actor": "user-a",
            }
        ],
    }
    task.update(overrides)
    return task


def test_task_read_access_requires_scope_membership_and_owner() -> None:
    service = TaskReadAccessService()
    project_access = _ProjectAccess()
    membership = _OrganizationMembership()

    service.require(
        task=_scoped_task(),
        principal=_principal(),
        project_access=project_access,
        organization_membership=membership,
    )
    assert len(project_access.calls) == 1

    with pytest.raises(TaskReadAccessError) as foreign_scope:
        service.require(
            task=_scoped_task(tenant_id="tenant-b"),
            principal=_principal(),
            project_access=project_access,
            organization_membership=membership,
        )
    assert foreign_scope.value.status_code == 404
    assert len(project_access.calls) == 1

    with pytest.raises(TaskReadAccessError) as foreign_owner:
        service.require(
            task=_scoped_task(
                history=[
                    {
                        "event_type": "task_ingested",
                        "actor": "user-b",
                    }
                ]
            ),
            principal=_principal(),
            project_access=project_access,
            organization_membership=membership,
        )
    assert foreign_owner.value.status_code == 404


def test_task_read_access_keeps_explicit_admin_compatibility() -> None:
    service = TaskReadAccessService()
    admin = _principal(
        subject_id="admin-a",
        tenant_id=None,
        project_id=None,
        roles=frozenset({"admin"}),
    )

    service.require(
        task={"id": "legacy-task"},
        principal=admin,
        project_access=None,
        organization_membership=None,
    )

    with pytest.raises(TaskReadAccessError) as normal_user:
        service.require(
            task={"id": "legacy-task"},
            principal=_principal(),
            project_access=_ProjectAccess(),
            organization_membership=_OrganizationMembership(),
        )
    assert normal_user.value.status_code == 404

    with pytest.raises(TaskReadAccessError) as scoped_admin:
        service.require(
            task=_scoped_task(tenant_id="tenant-b"),
            principal=_principal(roles=frozenset({"admin"})),
            project_access=_ProjectAccess(role="tenant_admin"),
            organization_membership=_OrganizationMembership(),
        )
    assert scoped_admin.value.status_code == 404

    project_access = _ProjectAccess(role="tenant_admin")
    service.require(
        task=_scoped_task(),
        principal=_principal(roles=frozenset({"admin"})),
        project_access=project_access,
        organization_membership=_OrganizationMembership(),
    )
    assert project_access.calls[0]["tenant_admin"] is True

    with pytest.raises(TaskReadAccessError) as stale_admin_membership:
        service.require(
            task=_scoped_task(),
            principal=_principal(roles=frozenset({"admin"})),
            project_access=_ProjectAccess(role="tenant_admin"),
            organization_membership=_OrganizationMembership(allowed=False),
        )
    assert stale_admin_membership.value.status_code == 404


def test_task_read_projections_drop_generic_read_secrets() -> None:
    sentinel = "TASK-READ-SECRET-SENTINEL"
    raw_task = {
        **_scoped_task(),
        "title": "Safe title",
        "description": "Safe description",
        "status": "todo",
        "callback_url": f"https://callback.invalid/{sentinel}",
        "callback_token": sentinel,
        "last_output": sentinel,
        "last_proposal": {"content": sentinel},
        "worker_execution_context": {
            "allowed_tools": ["read_file"],
            "profile_source": "explicit",
            "context": sentinel,
            "chunks": [{"content": sentinel}],
            "tokens": [sentinel],
            "source_catalog": {"content": sentinel},
            "callback_token": sentinel,
        },
        "verification_status": {
            "status": "passed",
            "source_catalog": {"content": sentinel},
            "answer_verification": {"content": sentinel},
            "content": sentinel,
        },
        "history": [
            {
                "event_type": "task_ingested",
                "actor": "user-a",
                "details": {
                    "source": "api",
                    "content": sentinel,
                    "source_catalog": {"content": sentinel},
                    "answer_verification": sentinel,
                    "callback_token": sentinel,
                },
            }
        ],
    }
    projection = TaskReadProjectionService()

    summary = projection.summary(raw_task)
    detail = projection.detail(
        raw_task,
        instruction_layers={
            "selected_profile": {
                "id": "profile-a",
                "name": "Safe profile",
                "prompt": sentinel,
            },
            "resolved_prompt": sentinel,
        },
    )
    tree = projection.tree(
        {
            "task": raw_task,
            "children": [{"task": raw_task, "children": []}],
        },
        can_read=lambda _task: True,
    )

    serialized = json.dumps(
        {"summary": summary, "detail": detail, "tree": tree},
        sort_keys=True,
    )
    assert sentinel not in serialized
    assert "callback_token" not in serialized
    assert "last_output" not in serialized
    assert "source_catalog" not in serialized
    assert "answer_verification" not in serialized
    assert detail["worker_execution_context"] == {
        "allowed_tools": ["read_file"],
        "profile_source": "explicit",
    }
    assert detail["verification_status"] == {"status": "passed"}


def test_access_pagination_applies_offset_to_authorized_rows(
    monkeypatch,
) -> None:
    foreign_rows = [
        _TaskRow(
            _scoped_task(
                id=f"foreign-{index:03d}",
                tenant_id="tenant-b",
                project_id="project-b",
            )
        )
        for index in range(_TASK_READ_SCAN_CHUNK_SIZE + 10)
    ]
    authorized_rows = [
        _TaskRow(_scoped_task(id=f"owned-{index}"))
        for index in range(1, 4)
    ]
    active_repo = _PagedTaskRepository(
        [*foreign_rows, *authorized_rows]
    )
    archived_repo = _PagedTaskRepository(
        [*foreign_rows, *authorized_rows]
    )
    monkeypatch.setattr(
        "agent.services.task_query_service.get_repository_registry",
        lambda: SimpleNamespace(
            task_repo=active_repo,
            archived_task_repo=archived_repo,
        ),
    )
    access = TaskReadAccessContext(
        principal=_principal(),
        project_access=_ProjectAccess(),
        organization_membership=_OrganizationMembership(),
        service=TaskReadAccessService(),
    )
    service = TaskQueryService()

    first = service.list_tasks(
        status_filter="",
        agent_filter=None,
        since_filter=None,
        until_filter=None,
        limit=2,
        offset=0,
        access=access,
    )
    second = service.list_tasks(
        status_filter="",
        agent_filter=None,
        since_filter=None,
        until_filter=None,
        limit=2,
        offset=2,
        access=access,
    )
    archived = service.list_archived_tasks(
        limit=2,
        offset=1,
        access=access,
    )

    assert [item["id"] for item in first] == ["owned-1", "owned-2"]
    assert [item["id"] for item in second] == ["owned-3"]
    assert [item["id"] for item in archived] == ["owned-2", "owned-3"]
    assert any(
        call["offset"] == _TASK_READ_SCAN_CHUNK_SIZE
        for call in active_repo.calls
    )
    assert any(
        call["offset"] == _TASK_READ_SCAN_CHUNK_SIZE
        for call in archived_repo.calls
    )
    assert all(
        call["tenant_id"] == "tenant-a"
        and call["project_id"] == "project-a"
        for call in [*active_repo.calls, *archived_repo.calls]
    )


def test_access_pagination_scan_is_bounded(monkeypatch) -> None:
    class EndlessForeignRepository(_PagedTaskRepository):
        def __init__(self) -> None:
            super().__init__([])

        def get_paged(self, *, limit: int, offset: int, **filters):
            self.calls.append(
                {"limit": limit, "offset": offset, **filters}
            )
            return [
                _TaskRow(
                    _scoped_task(
                        id=f"foreign-{offset + index}",
                        tenant_id="tenant-b",
                        project_id="project-b",
                    )
                )
                for index in range(limit)
            ]

    repository = EndlessForeignRepository()
    monkeypatch.setattr(
        "agent.services.task_query_service.get_repository_registry",
        lambda: SimpleNamespace(task_repo=repository),
    )
    access = SimpleNamespace(
        principal=_principal(),
        can_read=lambda _task: False,
    )

    result = TaskQueryService().list_tasks(
        status_filter="",
        agent_filter=None,
        since_filter=None,
        until_filter=None,
        limit=1,
        offset=0,
        access=access,
    )

    assert result == []
    assert sum(call["limit"] for call in repository.calls) == (
        _TASK_READ_SCAN_MAX_ROWS
    )
    assert all(
        call["limit"] <= _TASK_READ_SCAN_CHUNK_SIZE
        for call in repository.calls
    )


def test_foreign_task_is_hidden_from_generic_and_control_center_reads(
    client,
    monkeypatch,
    user_auth_header,
) -> None:
    sentinel = "FOREIGN-TASK-SECRET-SENTINEL"
    foreign_task = {
        **_scoped_task(tenant_id="tenant-b", project_id="project-b"),
        "description": sentinel,
        "callback_token": sentinel,
    }
    principal = _principal()
    runtime = SimpleNamespace(
        get_local_task_status=lambda _task_id: foreign_task
    )
    monkeypatch.setattr(
        "agent.routes.tasks.management.get_core_services",
        lambda: SimpleNamespace(task_runtime_service=runtime),
    )
    monkeypatch.setattr(
        "agent.routes.tasks.task_read_access.get_authenticated_source_control_principal",
        lambda: principal,
    )

    generic = client.get("/tasks/foreign-task", headers=user_auth_header)
    assert generic.status_code == 404
    assert sentinel not in generic.get_data(as_text=True)

    repositories = SimpleNamespace(
        task_repo=SimpleNamespace(get_by_id=lambda _task_id: foreign_task),
    )
    monkeypatch.setattr(
        "agent.routes.control_center_api._repos",
        lambda: repositories,
    )
    monkeypatch.setattr(
        "agent.routes.control_center_api.get_share_session_service",
        lambda: pytest.fail("foreign task must not create a session"),
    )

    detail = client.get("/api/tasks/foreign-task", headers=user_auth_header)
    session = client.post(
        "/api/tasks/foreign-task/sessions",
        headers=user_auth_header,
        json={},
    )
    assert detail.status_code == 404
    assert session.status_code == 404
    assert sentinel not in detail.get_data(as_text=True)
    assert sentinel not in session.get_data(as_text=True)


def test_authorized_control_center_task_detail_uses_closed_projection(
    client,
    monkeypatch,
    admin_auth_header,
) -> None:
    sentinel = "CONTROL-CENTER-SECRET-SENTINEL"
    task = {
        "id": "task-a",
        "title": "Safe task",
        "description": "Safe description",
        "callback_token": sentinel,
        "last_output": sentinel,
        "worker_execution_context": {
            "allowed_tools": ["read_file"],
            "context": sentinel,
        },
        "verification_status": {
            "status": "passed",
            "answer_verification": sentinel,
        },
    }
    policy = SimpleNamespace(
        id="policy-a",
        task_id="task-a",
        decision_type="allow",
        status="allowed",
        reasons=["safe_reason"],
        details={"source_catalog": sentinel},
        created_at=1.0,
    )
    artifact = SimpleNamespace(
        id="artifact-a",
        latest_media_type="text/plain",
        latest_filename="safe.txt",
        artifact_metadata={"task_id": "task-a", "content": sentinel},
        created_at=1.0,
        updated_at=1.0,
    )
    repositories = SimpleNamespace(
        task_repo=SimpleNamespace(get_by_id=lambda _task_id: task),
        agent_session_repo=SimpleNamespace(get_by_task_id=lambda _task_id: []),
        policy_decision_repo=SimpleNamespace(get_all=lambda: [policy]),
        artifact_repo=SimpleNamespace(get_all=lambda: [artifact]),
    )
    monkeypatch.setattr(
        "agent.routes.control_center_api._repos",
        lambda: repositories,
    )

    response = client.get("/api/tasks/task-a", headers=admin_auth_header)

    assert response.status_code == 200
    serialized = response.get_data(as_text=True)
    assert sentinel not in serialized
    assert "callback_token" not in serialized
    assert "last_output" not in serialized
    assert "source_catalog" not in serialized
    assert "answer_verification" not in serialized
    task_payload = response.get_json()["data"]["task"]
    assert task_payload["worker_execution_context"] == {
        "allowed_tools": ["read_file"]
    }


def test_scoped_control_center_create_records_authenticated_owner_once(
    client,
    app,
    monkeypatch,
    user_auth_header,
) -> None:
    sentinel = "BODY-PROVENANCE-MUST-NOT-BE-TRUSTED"
    principal = _principal(subject_id="owner-a")
    project_access = AllowProjectAccess(role="maintainer")
    stored: dict[str, object] = {}

    class TaskRepository:
        @staticmethod
        def save(task):
            stored[str(task.id)] = task
            return task

        @staticmethod
        def get_by_id(task_id: str):
            return stored.get(task_id)

    repositories = SimpleNamespace(
        task_repo=TaskRepository(),
        agent_session_repo=SimpleNamespace(get_by_task_id=lambda _task_id: []),
        policy_decision_repo=SimpleNamespace(get_all=lambda: []),
        artifact_repo=SimpleNamespace(get_all=lambda: []),
    )
    app.extensions["project_access_authority"] = project_access
    monkeypatch.setattr(
        "agent.routes.control_center_api._repos",
        lambda: repositories,
    )
    monkeypatch.setattr(
        "agent.routes.control_center_task_mutations.get_authenticated_source_control_principal",
        lambda: principal,
    )
    monkeypatch.setattr(
        "agent.routes.tasks.task_read_access.get_authenticated_source_control_principal",
        lambda: principal,
    )

    created = client.post(
        "/api/tasks",
        headers=user_auth_header,
        json={
            "title": "Scoped owner task",
            "project_id": "project-a",
            "source": sentinel,
            "created_by": sentinel,
            "actor": sentinel,
            "history": [
                {"event_type": "task_ingested", "actor": sentinel}
            ],
        },
    )

    assert created.status_code == 201
    task_id = created.get_json()["data"]["task"]["id"]
    persisted = stored[task_id]
    events = [
        event
        for event in persisted.history
        if event.get("event_type") == "task_ingested"
    ]
    assert len(events) == 1
    assert events[0]["actor"] == "owner-a"
    assert events[0]["details"] == {
        "source": "api",
        "channel": "control_center_task_management",
    }
    assert sentinel not in json.dumps(events)

    detail = client.get(
        f"/api/tasks/{task_id}",
        headers=user_auth_header,
    )
    assert detail.status_code == 200


def test_global_orchestration_read_model_is_admin_metadata_only(
    client,
    monkeypatch,
    admin_auth_header,
    user_auth_header,
) -> None:
    normal_user = client.get(
        "/tasks/orchestration/read-model",
        headers=user_auth_header,
    )
    assert normal_user.status_code == 403

    monkeypatch.setattr(
        "agent.routes.tasks.orchestration._services",
        lambda: pytest.fail("forbidden content request must fail before assembly"),
    )
    include_content = client.get(
        "/tasks/orchestration/read-model"
        "?artifact_flow_rag_include_content=true",
        headers=admin_auth_header,
    )
    assert include_content.status_code == 403
    assert include_content.get_json()["message"] == (
        "artifact_flow_rag_content_forbidden"
    )

    seen_overrides: list[dict] = []
    services = SimpleNamespace(
        task_queue_service=object(),
        task_claim_service=SimpleNamespace(
            orchestration_read_model=lambda **_kwargs: {}
        ),
    )
    tracking = SimpleNamespace(
        build_execution_reconciliation_snapshot=lambda: {},
        build_control_layer_observability_snapshot=lambda: {},
        build_artifact_flow_read_model=lambda *, overrides: (
            seen_overrides.append(overrides) or {}
        ),
    )
    monkeypatch.setattr(
        "agent.routes.tasks.orchestration._services",
        lambda: services,
    )
    monkeypatch.setattr(
        "agent.routes.tasks.orchestration.get_task_execution_tracking_service",
        lambda: tracking,
    )

    metadata_only = client.get(
        "/tasks/orchestration/read-model"
        "?artifact_flow_rag_include_content=false",
        headers=admin_auth_header,
    )
    assert metadata_only.status_code == 200
    assert seen_overrides == [{"rag_include_content": False}]
