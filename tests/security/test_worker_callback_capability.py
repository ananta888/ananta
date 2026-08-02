from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask, jsonify

from agent.auth import check_auth
from agent.routes import organization_planning as planning_routes
from agent.services import repository_registry
from agent.services.worker_result_callback_service import (
    WorkerResultCallbackError,
    WorkerResultCallbackService,
)
from agent.services.worker_result_capability_service import (
    WorkerResultCapabilityError,
    WorkerResultCapabilityService,
)
from agent.services.worker_task_proposal_result_adapter import (
    authoritative_assignment_scope,
    authoritative_assignment_scope_in_session,
)

SIGNING_SECRET = "worker-callback-capability-test-secret"


def _carrier() -> dict[str, Any]:
    proposals = [{"proposal_id": "proposal-callback"}]
    rendered = json.dumps(
        proposals,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return {
        "schema": "worker_task_proposals.v1",
        "payload_digest": f"sha256:{hashlib.sha256(rendered).hexdigest()}",
        "proposals": proposals,
    }


def _token(service: WorkerResultCapabilityService) -> str:
    return service.issue(
        worker_id="worker-a",
        source_task_id="task-a",
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        ttl_seconds=60,
    )


def test_capability_is_bound_to_worker_task_assignment_lease_and_scope() -> None:
    service = WorkerResultCapabilityService(signing_secret=SIGNING_SECRET)
    claims = service.verify(
        _token(service),
        source_task_id="task-a",
        assignment_id="assignment-a",
    )

    assert claims["worker_id"] == "worker-a"
    assert claims["source_task_id"] == "task-a"
    assert claims["assignment_id"] == "assignment-a"
    assert claims["dispatch_lease_id"] == "lease-a"
    assert set(claims["scopes"]) == {
        "worker.result.submit",
        "worker.task_proposal.submit",
    }
    assert "admin" not in claims["scopes"]
    assert "task.list" not in claims["scopes"]
    assert "task.followup.create" not in claims["scopes"]


@pytest.mark.parametrize(
    ("source_task_id", "assignment_id", "reason"),
    [
        ("task-other", "assignment-a", "worker_result_capability_task_mismatch"),
        ("task-a", "assignment-other", "worker_result_capability_assignment_mismatch"),
    ],
)
def test_capability_cannot_cross_task_or_assignment(
    source_task_id: str,
    assignment_id: str,
    reason: str,
) -> None:
    service = WorkerResultCapabilityService(signing_secret=SIGNING_SECRET)

    with pytest.raises(WorkerResultCapabilityError, match=reason):
        service.verify(
            _token(service),
            source_task_id=source_task_id,
            assignment_id=assignment_id,
        )


def test_expired_or_tampered_capability_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.services import worker_result_capability_service as capability_module

    now = 10_000
    monkeypatch.setattr(capability_module.time, "time", lambda: now)
    service = WorkerResultCapabilityService(signing_secret=SIGNING_SECRET)
    token = _token(service)

    monkeypatch.setattr(capability_module.time, "time", lambda: now + 61)
    with pytest.raises(WorkerResultCapabilityError, match="worker_result_capability_expired"):
        service.verify(token, source_task_id="task-a", assignment_id="assignment-a")

    parts = token.split(".")
    tampered = f"{parts[0]}.{parts[1]}.{parts[2][::-1]}"
    with pytest.raises(WorkerResultCapabilityError, match="worker_result_capability_signature_invalid"):
        service.verify(tampered, source_task_id="task-a", assignment_id="assignment-a")


def test_authoritative_assignment_marks_reassigned_lease_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = {
        "id": "task-a",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "organization_id": "organization-a",
        "goal_id": "goal-a",
        "unit_id": "unit-a",
        "team_id": "team-a",
        "role_slot_id": "slot-a",
        "status": "in_progress",
        "current_worker_job_id": "lease-current",
        "required_capabilities": [],
        "worker_execution_context": {
            "task_proposal_binding": {
                "assignment_id": "assignment-a",
                "dispatch_lease_id": "lease-stale",
                "worker_id": "worker-a",
                "role_template_ref": "developer@1",
                "proposal_policy": {},
            },
            "planning_lineage": {"source_category_item_ids": ["ITEM-A"]},
        },
    }
    registry = SimpleNamespace(
        task_repo=SimpleNamespace(get_by_id=lambda task_id: task if task_id == "task-a" else None)
    )
    monkeypatch.setattr(repository_registry, "get_repository_registry", lambda: registry)

    assignment, _policy = authoritative_assignment_scope(source_task_id="task-a")

    assert assignment.dispatch_lease_id == "lease-stale"
    assert assignment.lease_active is False


def test_authoritative_in_session_assignment_rejects_completed_worker_job() -> None:
    from agent.db_models import TaskDB, WorkerJobDB

    task = TaskDB(
        id="task-completed-job",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        goal_id="goal-a",
        unit_id="unit-a",
        team_id="team-a",
        role_slot_id="slot-a",
        status="completed",
        current_worker_job_id="lease-completed",
        worker_execution_context={
            "task_proposal_binding": {
                "assignment_id": "assignment-a",
                "dispatch_lease_id": "lease-completed",
                "worker_id": "worker-a",
                "role_template_ref": "developer@1",
                "proposal_policy": {},
            },
            "organization_routing": {
                "schema": "organization_routing_decision.v1",
                "selected_assignment_id": "role-assignment-a",
                "selected_agent_id": "worker-a",
                "selected_team_id": "team-a",
                "selected_role_slot_id": "slot-a",
            },
        },
    )
    job = WorkerJobDB(
        id="lease-completed",
        parent_task_id=task.id,
        subtask_id="assignment-a",
        worker_url="worker-a",
        status="completed",
        finished_at=1.0,
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def one_or_none(self):
            return self.value

    class SessionStub:
        def __init__(self):
            self.results = iter((task, job, SimpleNamespace(id="role-assignment-a")))

        def exec(self, _statement):
            return Result(next(self.results))

    assignment, _policy = authoritative_assignment_scope_in_session(
        session=SessionStub(),  # type: ignore[arg-type]
        source_task_id=task.id,
    )

    assert assignment.lease_active is False


def test_worker_capability_cannot_authenticate_a_normal_task_list_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_TOKEN_FILE", raising=False)
    app = Flask(__name__)
    app.config["AGENT_TOKEN"] = "normal-route-agent-token-value-32-bytes"

    @app.get("/api/tasks")
    @check_auth
    def task_list():
        return jsonify({"tasks": []})

    token = _token(WorkerResultCapabilityService(signing_secret=SIGNING_SECRET))
    response = app.test_client().get(
        "/api/tasks",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_normal_user_bearer_cannot_authenticate_worker_callback(
    client,
    admin_auth_header,
) -> None:
    response = client.post(
        "/tasks/task-a/subtask-callback",
        headers=admin_auth_header,
        json={"id": "assignment-a", "status": "completed"},
    )

    assert response.status_code == 401
    assert response.get_json()["message"] == "worker_result_capability_required"


def test_proposal_ingress_has_no_generic_auth_fallback_and_requires_closed_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = Flask(__name__)
    app.register_blueprint(planning_routes.organization_planning_bp)
    endpoint = "/api/worker-results/tasks/task-a/assignments/assignment-a/proposals"
    client = app.test_client()

    assert not hasattr(planning_routes.ingest_assignment_bound_worker_proposals, "__wrapped__")
    ordinary = client.post(
        endpoint,
        headers={"Authorization": "Bearer ordinary-admin-token"},
        json=_carrier(),
    )
    assert ordinary.status_code == 401
    assert ordinary.get_json()["reason_code"] == "worker_result_capability_required"

    class BoundCapabilityService:
        def verify(self, token: str, *, source_task_id: str, assignment_id: str) -> dict[str, Any]:
            assert token == "wrc1.payload.signature"
            return {
                "worker_id": "worker-a",
                "source_task_id": source_task_id,
                "assignment_id": assignment_id,
                "dispatch_lease_id": "lease-a",
                "scopes": ["worker.result.submit", "worker.task_proposal.submit"],
            }

    monkeypatch.setattr(planning_routes, "WorkerResultCapabilityService", BoundCapabilityService)
    monkeypatch.setattr(
        planning_routes,
        "ingest_callback_task_proposals",
        lambda **_kwargs: [
            {
                "proposal_id": "proposal-callback",
                "proposal_revision": 1,
                "proposal_digest": "sha256:" + "1" * 64,
                "payload_digest": "sha256:" + "2" * 64,
                "state": "submitted",
                "task_created": False,
                "queue_write": False,
            }
        ],
    )
    accepted = client.post(
        endpoint,
        headers={"Authorization": "Bearer wrc1.payload.signature"},
        json=_carrier(),
    )
    assert accepted.status_code == 202
    assert accepted.get_json()["proposals"][0]["status"] == "pending"
    assert accepted.get_json()["task_created"] is False
    assert accepted.get_json()["queue_write"] is False

    artifact_reference = client.post(
        endpoint,
        headers={"Authorization": "Bearer wrc1.payload.signature"},
        json={
            "schema": "worker_task_proposals_ref.v1",
            "artifact_version_ref": "artifact:proposal-carrier",
            "payload_digest": "sha256:" + "3" * 64,
            "proposal_count": 1,
        },
    )
    assert artifact_reference.status_code == 422
    assert artifact_reference.get_json()["reason_code"] == "worker_task_proposals_carrier_invalid"


def test_worker_callback_consumes_live_job_and_allows_only_exact_replay(
    db_session,
) -> None:
    from agent.db_models import TaskDB, WorkerJobDB

    task = TaskDB(
        id="worker-callback-parent",
        status="in_progress",
        current_worker_job_id="worker-callback-lease",
        worker_execution_context={
            "task_proposal_binding": {
                "assignment_id": "worker-callback-assignment",
                "dispatch_lease_id": "worker-callback-lease",
                "worker_id": "https://worker.example.test",
            }
        },
    )
    job = WorkerJobDB(
        id="worker-callback-lease",
        parent_task_id=task.id,
        subtask_id="worker-callback-assignment",
        worker_url="https://worker.example.test",
        status="running",
    )
    db_session.add(task)
    db_session.add(job)
    db_session.commit()
    claims = {
        "source_task_id": task.id,
        "assignment_id": "worker-callback-assignment",
        "dispatch_lease_id": job.id,
        "worker_id": job.worker_url,
        "jti": "worker-callback-capability-jti",
        "scopes": ["worker.result.submit", "worker.task_proposal.submit"],
    }
    payload = {
        "id": "worker-callback-assignment",
        "status": "completed",
        "worker_job_id": "untrusted-worker-value",
        "last_output": "done",
    }
    service = WorkerResultCallbackService()

    first = service.accept(
        task_id=task.id,
        payload=payload,
        capability_claims=claims,
    )
    replay = service.accept(
        task_id=task.id,
        payload=payload,
        capability_claims=claims,
    )

    assert first == {"status": "updated", "replayed": False}
    assert replay == {"status": "updated", "replayed": True}
    with pytest.raises(
        WorkerResultCallbackError,
        match="worker_result_callback_idempotency_conflict",
    ):
        service.accept(
            task_id=task.id,
            payload={**payload, "last_output": "changed"},
            capability_claims=claims,
        )


def test_worker_callback_rejects_finished_job_without_receipt(db_session) -> None:
    from agent.db_models import TaskDB, WorkerJobDB

    task = TaskDB(
        id="worker-callback-stale-parent",
        status="in_progress",
        current_worker_job_id="worker-callback-stale-lease",
        worker_execution_context={
            "task_proposal_binding": {
                "assignment_id": "worker-callback-stale-assignment",
                "dispatch_lease_id": "worker-callback-stale-lease",
                "worker_id": "https://worker-stale.example.test",
            }
        },
    )
    job = WorkerJobDB(
        id="worker-callback-stale-lease",
        parent_task_id=task.id,
        subtask_id="worker-callback-stale-assignment",
        worker_url="https://worker-stale.example.test",
        status="completed",
        finished_at=1.0,
    )
    db_session.add(task)
    db_session.add(job)
    db_session.commit()

    with pytest.raises(
        WorkerResultCallbackError,
        match="worker_result_callback_dispatch_lease_inactive",
    ):
        WorkerResultCallbackService().accept(
            task_id=task.id,
            payload={
                "id": "worker-callback-stale-assignment",
                "status": "completed",
            },
            capability_claims={
                "source_task_id": task.id,
                "assignment_id": "worker-callback-stale-assignment",
                "dispatch_lease_id": job.id,
                "worker_id": job.worker_url,
                "jti": "worker-callback-stale-jti",
                "scopes": ["worker.result.submit"],
            },
        )
