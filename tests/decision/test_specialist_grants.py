"""Hub grants require Registry identity, current SQL lease, admin auth and audit."""

import time
from types import SimpleNamespace

import pytest
from flask import Flask, g, request
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models.tasks import TaskDB
from agent.db_models.workers import WorkerJobDB, WorkerSlotLeaseDB
from agent.repositories.specialist_dispatch_lease import SqlSpecialistDispatchLeaseAuthority
from agent.routes.specialist_inference import specialist_inference_bp
from agent.services.specialist_inference_grant_service import GrantAuditUnavailable, SpecialistInferenceGrantService
from ananta_contracts.specialist_inference_assignment import verify_assignment
from tests.decision.test_specialist_inference_assignments import MODEL, issued_token


@pytest.fixture
def grant(tmp_path):
    _, key, _, signer, bindings = issued_token(tmp_path, clock=time.time)
    events = []
    service = SpecialistInferenceGrantService(
        registry=signer._registry,
        private_key=key,
        lease_factory=lambda **scope: SimpleNamespace(require_current=lambda **binding: time.time() + 30),
        audit=lambda action, details: events.append((action, details)),
    )
    return service, key, {k: v for k, v in bindings.items() if k != "model_revision"}, events


def test_grant_is_exact_registry_bound_and_never_logs_token_or_input(grant):
    service, key, payload, events = grant
    result = service.issue(payload)
    verified = verify_assignment(
        result["assignment_token"], key.public_key(), body=payload["body"], model_revision=MODEL
    )
    assert verified["evidence"]["run_id"] == payload["run_id"]
    assert result["grants_permission"] is False
    assert len(events) == 1 and "body" not in events[0][1] and "assignment_token" not in events[0][1]
    with pytest.raises(ValueError):
        service.issue({**payload, "body": {**payload["body"], "state": "tampered"}})


def test_grant_cannot_be_returned_when_audit_fails(grant):
    service, _, payload, _ = grant

    def failed(*args):
        raise OSError("bounded audit failure")

    service._audit = failed
    with pytest.raises(GrantAuditUnavailable, match="audit_unavailable"):
        service.issue(payload)


@pytest.mark.parametrize("changed", [{"tenant_id": {}}, {"task_id": ""}, {"body": None}])
def test_malformed_grant_bindings_fail_before_audit(grant, changed):
    service, _, payload, events = grant
    with pytest.raises(ValueError, match="request_invalid"):
        service.issue({**payload, **changed})
    assert events == []


def test_grant_api_is_admin_only_closed_and_hub_bound(grant, monkeypatch):
    service, _, payload, _ = grant
    app = Flask(__name__)
    app.config.update(TESTING=True, ROLE="hub")
    app.extensions["specialist_inference_grants"] = service
    app.register_blueprint(specialist_inference_bp)
    monkeypatch.setattr("agent.auth.log_audit", lambda *args, **kwargs: None)

    @app.before_request
    def role_fixture():
        if request.headers.get("X-Test-Role"):
            g.is_admin = request.headers["X-Test-Role"] == "admin"

    client = app.test_client()
    path = "/api/specialist-inference/assignments"
    assert client.post(path, json=payload).status_code == 401
    assert client.post(path, json=payload, headers={"X-Test-Role": "viewer"}).status_code == 403
    headers = {"X-Test-Role": "admin"}
    assert client.post(path, json=payload, headers=headers).status_code == 200
    assert client.post(path, json={**payload, "unknown": True}, headers=headers).status_code == 422
    assert client.post(path, data="x" * 32769, headers=headers).status_code == 413

    def unavailable(*args):
        raise OSError("sensitive internal storage diagnostic")

    service._audit = unavailable
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 503
    assert b"assignment_token" not in response.data and b"sensitive" not in response.data
    app.config["ROLE"] = "worker"
    assert client.post(path, json=payload, headers=headers).status_code == 503


@pytest.fixture
def lease_rows(tmp_path):
    db = create_engine("sqlite:///" + str(tmp_path / "leases.db"))
    SQLModel.metadata.create_all(db, tables=[TaskDB.__table__, WorkerJobDB.__table__, WorkerSlotLeaseDB.__table__])
    with Session(db) as session:
        session.add(
            TaskDB(id="task", tenant_id="tenant", project_id="project", status="running", current_worker_job_id="job")
        )
        session.add(
            WorkerJobDB(
                id="job",
                parent_task_id="task",
                worker_url="http://worker",
                status="running",
                selected_worker_id="worker",
                slot_lease_id="lease",
            )
        )
        session.add(
            WorkerSlotLeaseDB(
                id="lease", worker_job_id="job", parent_task_id="task", worker_id="worker", deadline_at=200
            )
        )
        session.commit()
    return db


def test_sql_authority_accepts_only_exact_live_task_job_slot_scope(lease_rows):
    authority = SqlSpecialistDispatchLeaseAuthority(
        tenant_id="tenant", project_id="project", engine=lambda: lease_rows, clock=lambda: 100
    )
    assert authority.require_current(task_id="task", assignment_id="job", dispatch_lease_id="lease") == 200
    for binding in ({"task_id": "other"}, {"assignment_id": "other"}, {"dispatch_lease_id": "other"}):
        with pytest.raises(ValueError, match="not_current"):
            authority.require_current(
                **({"task_id": "task", "assignment_id": "job", "dispatch_lease_id": "lease"} | binding)
            )


def test_sql_authority_accepts_job_created_by_hub_delegation(lease_rows, monkeypatch):
    from agent.services import worker_job_service as jobs

    def save(job):
        with Session(lease_rows) as session:
            session.add(job)
            session.commit()
            session.refresh(job)
        return job

    monkeypatch.setattr(jobs, "worker_job_repo", SimpleNamespace(save=save))
    job = jobs.WorkerJobService().create_worker_job(
        parent_task_id="task",
        subtask_id="worker-task",
        worker_url="http://worker",
        context_bundle_id=None,
        allowed_tools=[],
        expected_output_schema={},
        selection_decision={"selected_worker_id": "worker"},
        scheduling_decision={"status": "active", "slot_lease_id": "lease"},
    )
    assert job.status == "delegated"
    with Session(lease_rows) as session:
        task = session.get(TaskDB, "task")
        lease = session.get(WorkerSlotLeaseDB, "lease")
        task.current_worker_job_id = job.id
        lease.worker_job_id = job.id
        session.add(task)
        session.add(lease)
        session.commit()

    authority = SqlSpecialistDispatchLeaseAuthority(
        tenant_id="tenant", project_id="project", engine=lambda: lease_rows, clock=lambda: 100
    )
    assert authority.require_current(task_id="task", assignment_id=job.id, dispatch_lease_id="lease") == 200


@pytest.mark.parametrize(
    "model,field,value",
    [
        (TaskDB, "tenant_id", "other"),
        (TaskDB, "project_id", "other"),
        (TaskDB, "status", "cancelled"),
        (TaskDB, "current_worker_job_id", "stale"),
        (WorkerJobDB, "status", "failed"),
        (WorkerJobDB, "slot_lease_id", "stale"),
        (WorkerSlotLeaseDB, "status", "released"),
        (WorkerSlotLeaseDB, "deadline_at", 100),
        (WorkerSlotLeaseDB, "worker_id", "other"),
    ],
)
def test_stale_cancelled_expired_or_cross_scope_lease_cannot_sign(lease_rows, model, field, value):
    ids = {TaskDB: "task", WorkerJobDB: "job", WorkerSlotLeaseDB: "lease"}
    with Session(lease_rows) as session:
        row = session.get(model, ids[model])
        setattr(row, field, value)
        session.add(row)
        session.commit()
    authority = SqlSpecialistDispatchLeaseAuthority(
        tenant_id="tenant", project_id="project", engine=lambda: lease_rows, clock=lambda: 100
    )
    with pytest.raises(ValueError, match="not_current"):
        authority.require_current(task_id="task", assignment_id="job", dispatch_lease_id="lease")


def test_strict_audit_propagates_storage_failure_without_changing_legacy_default(monkeypatch):
    from agent.common import audit

    monkeypatch.setattr(audit, "_engine", lambda: None)

    def unavailable(*args):
        raise OSError("audit storage unavailable")

    monkeypatch.setattr(audit, "Session", unavailable)
    assert audit.log_audit("synthetic-test", {}) is None
    with pytest.raises(OSError, match="storage unavailable"):
        audit.log_audit("synthetic-test", {}, raise_errors=True)
