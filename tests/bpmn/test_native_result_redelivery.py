"""Real signed Worker retrieval and SQL Hub admission under original dispatch authority."""

import base64
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlmodel import Session, select

from agent.db_models import AgentInfoDB
from agent.db_models.native_dispatch import NativeDispatchDB
from agent.db_models.workflow_runtime import (
    WorkflowAuthorizationGrantDB,
    WorkflowExecutionOwnershipDB,
    WorkflowWorkerAssignmentDB,
)
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from agent.services.native_dispatch_result_recovery import NativeResultRecoveryPorts, recover_native_dispatch_results
from agent.services.native_result_forwarding import validate_forwarded_native_result
from agent.services.workflow_worker_assignment_service import SQLAlchemyWorkflowWorkerAssignmentStore
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing, Ed25519VerificationKeyRing
from tests.bpmn.test_native_result_admission import native  # noqa: F401
from tests.pi.test_pi_result_completion_policy import admission, complete  # noqa: F401
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
from worker.runtime.native_graph.result_store import SQLiteNativeResultStore

# ruff: noqa: F811 -- imported pytest fixtures.


@pytest.fixture
def redelivery(native, tmp_path, monkeypatch):
    task = native.repository.get_by_id(native.task_id)
    assignments = SQLAlchemyWorkflowWorkerAssignmentStore(native.engine)
    assignment = assignments.get(tenant_id=task.tenant_id, run_id=native.command.run_id,
                                  step_id=native.command.node.node_id)
    journal = SQLAlchemyNativeDispatchJournal(native.engine)
    claim = journal.begin(task=task, assignment=assignment)
    journal.settle(task=task, claim=claim, accepted=False)
    task.status, task.status_reason_code = "waiting_for_review", "native_autopilot_dispatch_blocked"
    native.repository.save(task)
    directory = tmp_path / "worker-private"
    directory.mkdir(mode=0o700)
    store = SQLiteNativeResultStore(directory / "results.sqlite3")
    store.reserve(task.model_dump(mode="json"))
    store.publish(task.model_dump(mode="json"), native.response)
    # Reopen persisted Worker output as a restarted execution process would.
    store = SQLiteNativeResultStore(store.path)
    token = "synthetic-worker-token-" + "x" * 40
    worker = SimpleNamespace(name=assignment.worker_id, url=assignment.worker_url, role="worker", token=token,
                             registration_validated=True, registration_provenance="hub_credential_registry_v1",
                             authorized_capabilities=["workflow.adapter.native"])
    from agent.services.workflow_worker_service_auth import STRICT_WORKER_REGISTRATION_PROVENANCE

    worker.registration_provenance = STRICT_WORKER_REGISTRATION_PROVENANCE
    signer = Ed25519SigningKeyRing({"synthetic": base64.b64encode(b"s" * 32).decode()}, active_key_id="synthetic")
    verifier = Ed25519VerificationKeyRing.from_mapping(signer.verification_mapping())
    app = Flask(__name__)
    app.config["ROLE"] = "worker"
    calls = []

    def consumer(_task):
        raise AssertionError("Redelivery must not execute a command")

    app.register_blueprint(create_native_execution_blueprint(
        token_provider=lambda: token, recipient_provider=lambda: (worker.name, worker.url),
        verification_provider=lambda: verifier, consumer=consumer, result_store_provider=lambda: store,
    ))

    def fetch(url, endpoint, payload, **options):
        calls.append(endpoint)
        response = app.test_client().post(endpoint, json=payload,
                                         headers={"Authorization": "Bearer " + options["token"]})
        assert response.status_code == 200
        return response.json

    def accept(**values):
        validate_forwarded_native_result(task_id=task.id, dispatched_task=values["task"],
                                         response=values["response"], load_task=native.repository.get_by_id)
        native.response = values["response"]
        complete(native)

    monkeypatch.setattr("agent.services.native_dispatch_result_recovery.time.time", lambda: 100)
    ports = NativeResultRecoveryPorts(
        repository=native.repository, journal=journal, assignments=assignments,
        workers=SimpleNamespace(get_by_url=lambda url: worker), signing_keys=lambda: signer,
        transport=SimpleNamespace(forward_task=fetch), accept_result=accept,
    )
    return SimpleNamespace(native=native, ports=ports, task=task, calls=calls)


def test_stored_worker_result_closes_original_journal_without_execution(redelivery):
    run = redelivery
    result = recover_native_dispatch_results(ports=run.ports, tenant_id=run.task.tenant_id)
    assert result["accepted"] == 1 and result["unresolved"] == 0
    assert run.calls == ["/api/internal/native-tasks/result"]
    task = run.native.repository.get_by_id(run.task.id)
    assert task.status == "completed" and task.verification_status["native_result_receipt"]
    with Session(run.native.engine) as session:
        assert session.exec(select(NativeDispatchDB)).one().state == "accepted"
    assert recover_native_dispatch_results(ports=run.ports, tenant_id=run.task.tenant_id)["results"] == []


@pytest.mark.parametrize("model,field,value", [
    (WorkflowExecutionOwnershipDB, "lease_expires_at", 1),
    (WorkflowExecutionOwnershipDB, "fencing_token", 99),
    (WorkflowWorkerAssignmentDB, "revision", 2),
    (WorkflowAuthorizationGrantDB, "grant_digest", "a" * 64),
    (AgentInfoDB, "registration_validated", False),
])
def test_redelivery_does_not_revive_stale_or_revoked_authority(redelivery, model, field, value):
    run = redelivery
    with Session(run.native.engine) as session:
        row = session.exec(select(model)).one()
        setattr(row, field, value)
        session.add(row)
        session.commit()
    result = recover_native_dispatch_results(ports=run.ports, tenant_id=run.task.tenant_id)
    assert result["unresolved"] == 1 and result["accepted"] == 0
    task = run.native.repository.get_by_id(run.task.id)
    assert task.status == "waiting_for_review" and not task.verification_status.get("native_result_receipt")


def test_non_transport_review_and_foreign_tenant_are_not_recovery_authority(redelivery):
    run = redelivery
    assert recover_native_dispatch_results(ports=run.ports, tenant_id="foreign-tenant")["results"] == []
    task = run.native.repository.get_by_id(run.task.id)
    task.status_reason_code = "mandatory_human_review"
    run.native.repository.save(task)
    result = recover_native_dispatch_results(ports=run.ports, tenant_id=run.task.tenant_id)
    assert result["unresolved"] == 1 and not run.calls
