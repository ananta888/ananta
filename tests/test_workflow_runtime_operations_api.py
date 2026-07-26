from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Mapping

import jwt
import pytest
from werkzeug.security import generate_password_hash

from agent.auth import generate_token
from agent.config import settings
from agent.db_models import UserDB
from agent.repository import user_repo
from agent.services.run_control_service import RunControlService
from agent.services.user_session_tokens import local_user_tenant_id
from agent.services.workflow_runtime.errors import ContractValidationError
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent
from agent.services.workflow_runtime_command_service import (
    RunControlRuntimeCommandGateway,
    RuntimeOperationCommandRequest,
    WorkflowRuntimeCommandService,
)
from agent.services.workflow_runtime_operations_models import (
    RuntimeGateView,
    WorkflowRuntimeOperationRecord,
)
from agent.services.workflow_runtime_read_model_service import (
    InMemoryWorkflowRuntimeReadModelRepository,
    WorkflowRuntimeReadModelService,
    get_workflow_runtime_read_model_service,
    reset_workflow_runtime_read_model_service,
)


@pytest.fixture(autouse=True)
def _clean_runtime_operations_read_model():
    reset_workflow_runtime_read_model_service()
    yield
    reset_workflow_runtime_read_model_service()


def _headers(*, tenant_id: str, subject: str = "operator-a") -> dict[str, str]:
    token = generate_token(
        {"sub": subject, "role": "user", "tenant_id": tenant_id},
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def _snapshot(
    run_id: str,
    *,
    tenant_id: str = "tenant-a",
    status: str = "running",
    updated_at: float | None = None,
    evidence: list[dict[str, Any]] | None = None,
    gates: list[dict[str, Any]] | None = None,
    degraded: bool = False,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "workflow_id": f"workflow-{run_id}",
        "task_id": f"task-{run_id}",
        "runtime": "langgraph",
        "mode": "compiled",
        "status": status,
        "capabilities": [
            {"name": "checkpoint", "status": "supported"},
            {"name": "durable_resume", "status": "partial", "reason_code": "native_gap"},
        ],
        "fallbacks": (
            [{"from": "langgraph", "to": "native", "reason_code": "compiled_failed", "approved": True}]
            if degraded
            else []
        ),
        "cost_micros": 1234,
        "latency_ms": 42.5,
        "recovery": {"status": "ready", "strategy": "checkpoint", "attempts": 1},
        "gates": gates or [],
        "evidence": evidence or [],
        "parity_gaps": ([{"code": "native_interrupt_gap", "category": "parity"}] if degraded else []),
        "updated_at": updated_at if updated_at is not None else time.time(),
        "stale_after_seconds": 30,
        "source_sequence": 7,
    }


def test_operations_api_is_strictly_authenticated_and_empty_is_explicit(client):
    unauthorized = client.get("/api/workflow-runtime/operations")
    assert unauthorized.status_code == 401

    response = client.get(
        "/api/workflow-runtime/operations",
        headers=_headers(tenant_id="tenant-a"),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["schema"] == "ananta.workflow_runtime_operations_list.v1"
    assert payload["runs"] == []
    assert payload["summary"]["total_runs"] == 0


def test_operations_api_rejects_authenticated_user_without_tenant_identity(client):
    token = generate_token(
        {"sub": "operator-without-tenant", "role": "admin"},
        settings.secret_key,
    )

    response = client.get(
        "/api/workflow-runtime/operations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.get_json()["reason_code"] == "workflow_runtime_identity_required"


def test_operations_api_rejects_oversized_identity_instead_of_truncating(client):
    token = generate_token(
        {"sub": "operator", "tenant_id": "t" * 161, "role": "user"},
        settings.secret_key,
    )

    response = client.get(
        "/api/workflow-runtime/operations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.get_json()["reason_code"] == "workflow_runtime_identity_required"


@pytest.mark.parametrize("claim_name", ["sub", "tenant_id"])
def test_operations_api_rejects_noncanonical_claim_identity(client, claim_name: str):
    claims = {"sub": "operator", "tenant_id": "tenant-a", "role": "user"}
    claims[claim_name] = f" {claims[claim_name]}"
    token = generate_token(claims, settings.secret_key)

    response = client.get(
        "/api/workflow-runtime/operations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.get_json()["reason_code"] == "workflow_runtime_identity_required"


@pytest.mark.parametrize(
    ("field_name", "value", "reason_code"),
    [
        ("tenant_id", "t" * 161, "tenant_id_too_long"),
        ("run_id", "r" * 161, "run_id_too_long"),
        ("tenant_id", " tenant-a", "tenant_id_not_canonical"),
        ("run_id", "run-a\n", "run_id_not_canonical"),
    ],
)
def test_read_model_rejects_noncanonical_or_oversized_security_identities(
    field_name: str,
    value: str,
    reason_code: str,
):
    snapshot = _snapshot("run-identity")
    snapshot[field_name] = value

    with pytest.raises(ValueError, match=reason_code):
        WorkflowRuntimeOperationRecord.from_mapping(snapshot)


def test_read_model_repository_revalidates_direct_dataclass_records_before_upsert():
    repository = InMemoryWorkflowRuntimeReadModelRepository()
    valid = WorkflowRuntimeOperationRecord.from_mapping(_snapshot("run-direct-record"))
    invalid_records = (
        replace(valid, tenant_id=" tenant-a"),
        replace(
            valid,
            gates=(RuntimeGateView(gate_id=" gate-direct", label="Invalid gate"),),
        ),
    )

    for record in invalid_records:
        with pytest.raises(ValueError, match="not_canonical"):
            repository.upsert(record)

    assert repository.list_for_tenant(tenant_id="tenant-a") == ()


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "reason_code"),
    [
        ("tenant_id", " tenant-a", "tenant_id_not_canonical"),
        ("tenant_id", 7, "tenant_id_not_canonical"),
        ("tenant_id", "t" * 161, "tenant_id_too_long"),
        ("workflow_id", "workflow-a ", "workflow_id_not_canonical"),
        ("workflow_id", 7, "workflow_id_not_canonical"),
        ("workflow_id", "w" * 161, "workflow_id_too_long"),
        ("run_id", " run-a", "run_id_not_canonical"),
        ("run_id", 7, "run_id_not_canonical"),
        ("run_id", "r" * 161, "run_id_too_long"),
    ],
)
def test_canonical_events_reject_raw_identity_aliases_without_normalizing(
    field_name: str,
    invalid_value: Any,
    reason_code: str,
):
    kwargs: dict[str, Any] = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "event_type": "workflow.run.started",
        "correlation_id": "correlation-a",
        "causation_id": "causation-a",
    }
    invalid_kwargs = {**kwargs, field_name: invalid_value}

    with pytest.raises(ContractValidationError) as build_error:
        CanonicalWorkflowEvent.build(**invalid_kwargs)
    assert reason_code in {issue.code for issue in build_error.value.issues}

    raw = CanonicalWorkflowEvent.build(**kwargs).with_sequence(1).to_dict()
    raw[field_name] = invalid_value
    with pytest.raises(ContractValidationError) as load_error:
        CanonicalWorkflowEvent.from_mapping(raw)
    assert reason_code in {issue.code for issue in load_error.value.issues}


def test_local_login_issues_explicit_tenant_and_can_read_operations(client):
    username = "runtime-operator-local"
    password = "LocalRuntimePassword123!"
    user_repo.save(
        UserDB(
            username=username,
            password_hash=generate_password_hash(password),
            role="user",
        )
    )

    login = client.post("/login", json={"username": username, "password": password})

    assert login.status_code == 200
    access_token = login.get_json()["data"]["access_token"]
    claims = jwt.decode(access_token, settings.secret_key, algorithms=["HS256"])
    assert claims["tenant_id"] == local_user_tenant_id(username)

    operations = client.get(
        "/api/workflow-runtime/operations",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert operations.status_code == 200
    assert operations.get_json()["schema"] == "ananta.workflow_runtime_operations_list.v1"


def test_local_login_rejects_legacy_username_that_would_collapse_tenant_identity(client):
    password = "LocalRuntimePassword123!"
    canonical_username = "runtime-operator-collision"
    noncanonical_username = f" {canonical_username} "
    for username in (canonical_username, noncanonical_username):
        user_repo.save(
            UserDB(
                username=username,
                password_hash=generate_password_hash(password),
                role="user",
            )
        )

    canonical_login = client.post(
        "/login",
        json={"username": canonical_username, "password": password},
    )
    rejected_login = client.post(
        "/login",
        json={"username": noncanonical_username, "password": password},
    )

    assert canonical_login.status_code == 200
    assert rejected_login.status_code == 409
    assert rejected_login.get_json()["message"] == "user_session_username_not_canonical"
    assert rejected_login.get_json()["data"]["reason_code"] == "user_session_username_not_canonical"
    assert "access_token" not in (rejected_login.get_json().get("data") or {})


def test_operations_read_model_filters_degraded_stale_and_never_claims_unverified_success(client):
    service = get_workflow_runtime_read_model_service()
    service.record_snapshot(
        _snapshot(
            "run-unverified",
            status="completed",
            degraded=True,
            evidence=[
                {
                    "evidence_id": "ev-unverified",
                    "kind": "test_gate",
                    "status": "unverified",
                    "summary": "Bearer abc.secret.token password=hunter2",
                }
            ],
        )
    )
    service.record_snapshot(
        _snapshot(
            "run-stale",
            updated_at=time.time() - 120,
            evidence=[{"evidence_id": "ev-stale", "kind": "probe", "status": "verified"}],
        )
    )
    service.record_snapshot(_snapshot("run-other", tenant_id="tenant-b"))

    headers = _headers(tenant_id="tenant-a")
    degraded = client.get(
        "/api/workflow-runtime/operations?health=degraded",
        headers=headers,
    )
    assert degraded.status_code == 200
    payload = degraded.get_json()
    assert [item["run_id"] for item in payload["runs"]] == ["run-unverified"]
    run = payload["runs"][0]
    assert run["status"] == "completed"
    assert run["outcome_claim"] == "unverified"
    assert "success_without_verified_evidence" in run["degraded_reasons"]
    assert "abc.secret.token" not in run["evidence"][0]["summary"]
    assert "hunter2" not in run["evidence"][0]["summary"]
    assert "tenant_id" not in run

    stale = client.get(
        "/api/workflow-runtime/operations?health=stale",
        headers=headers,
    ).get_json()
    assert [item["run_id"] for item in stale["runs"]] == ["run-stale"]
    assert stale["runs"][0]["stale"] is True
    assert all(item["run_id"] != "run-other" for item in stale["runs"])


def test_cross_tenant_detail_is_not_disclosed_and_invalid_filters_are_rejected(client):
    get_workflow_runtime_read_model_service().record_snapshot(_snapshot("run-private", tenant_id="tenant-a"))
    cross_tenant = client.get(
        "/api/workflow-runtime/operations/runs/run-private",
        headers=_headers(tenant_id="tenant-b", subject="operator-b"),
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.get_json()["reason_code"] == "runtime_run_not_found"

    invalid = client.get(
        "/api/workflow-runtime/operations?health=everything",
        headers=_headers(tenant_id="tenant-a"),
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["reason_code"] == "runtime_operations_health_filter_invalid"


def test_runtime_detail_does_not_alias_noncanonical_run_path(client):
    get_workflow_runtime_read_model_service().record_snapshot(_snapshot("run-private"))

    response = client.get(
        "/api/workflow-runtime/operations/runs/%20run-private",
        headers=_headers(tenant_id="tenant-a"),
    )

    assert response.status_code == 404
    assert response.get_json()["reason_code"] == "runtime_run_not_found"


def test_read_model_rebuilds_runtime_evaluation_from_canonical_hub_events():
    service = WorkflowRuntimeReadModelService(InMemoryWorkflowRuntimeReadModelRepository())

    def event(sequence: int, event_type: str, payload: dict[str, Any] | None = None):
        return CanonicalWorkflowEvent.build(
            tenant_id="tenant-a",
            workflow_id="workflow-events",
            run_id="run-events",
            event_type=event_type,
            correlation_id="correlation-events",
            causation_id="cause-events",
            dedupe_key=f"event-{sequence}",
            payload=payload,
            occurred_at=float(sequence),
        ).with_sequence(sequence)

    record = service.record_from_events(
        [
            event(1, "workflow.run.started"),
            event(
                2,
                "workflow.evidence.recorded",
                {
                    "evidence_id": "ev-events",
                    "kind": "acceptance",
                    "status": "verified",
                    "summary": "Hub verified",
                },
            ),
            event(3, "workflow.run.completed"),
        ],
        runtime_metadata={
            "task_id": "task-events",
            "runtime": "temporal",
            "mode": "durable",
            "capabilities": ["recovery"],
            "recovery": {"status": "ready", "strategy": "history_replay"},
        },
    )
    projected = service.get_run(tenant_id="tenant-a", run_id="run-events", now=3.0)
    assert projected is not None
    assert record.source_sequence == 3
    assert projected["status"] == "completed"
    assert projected["outcome_claim"] == "completed"
    assert projected["verified_evidence_count"] == 1
    assert projected["runtime"] == "temporal"


class _RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        return {
            "command_id": "cmd-1",
            "type": kwargs["command_type"],
            "status": "accepted",
            "run_id": kwargs["run_id"],
        }


def test_runtime_commands_require_bound_verified_evidence_and_approval(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    read_models = get_workflow_runtime_read_model_service()
    read_models.record_snapshot(
        _snapshot(
            "run-command",
            evidence=[
                {"evidence_id": "ev-ok", "kind": "acceptance", "status": "verified"},
                {"evidence_id": "ev-no", "kind": "claim", "status": "unverified"},
            ],
            gates=[
                {
                    "gate_id": "gate-ops",
                    "label": "Operator approval",
                    "status": "approved",
                    "approval_id": "approval-1",
                    "required_evidence_refs": ["ev-ok"],
                    "allowed_commands": ["retry_run_or_task"],
                    "expires_at": time.time() + 300,
                }
            ],
        )
    )
    audit_events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "agent.services.workflow_runtime_command_service.log_audit",
        lambda action, details: audit_events.append((action, details)),
    )
    gateway = _RecordingGateway()
    command_service = WorkflowRuntimeCommandService(read_models=read_models, gateway=gateway)
    monkeypatch.setattr(
        "agent.routes.workflow_runtime_operations.get_workflow_runtime_command_service",
        lambda: command_service,
    )
    headers = {**_headers(tenant_id="tenant-a"), "Idempotency-Key": "retry-command-0001"}

    missing_evidence = client.post(
        "/api/workflow-runtime/operations/runs/run-command/commands",
        headers=headers,
        json={
            "type": "retry_run_or_task",
            "approval_id": "approval-1",
            "evidence_refs": ["ev-no"],
        },
    )
    assert missing_evidence.status_code == 422
    assert missing_evidence.get_json()["reason_code"] == "runtime_command_evidence_unverified"
    assert gateway.calls == []

    accepted = client.post(
        "/api/workflow-runtime/operations/runs/run-command/commands",
        headers=headers,
        json={
            "type": "retry_run_or_task",
            "approval_id": "approval-1",
            "evidence_refs": ["ev-ok"],
        },
    )
    assert accepted.status_code == 202
    assert accepted.get_json()["command"]["status"] == "accepted"
    assert len(gateway.calls) == 1
    call = gateway.calls[0]
    assert call["task_id"] == "task-run-command"
    assert call["idempotency_key"].startswith("runtime-ops:v1:")
    assert len(call["idempotency_key"]) == len("runtime-ops:v1:") + 64
    assert call["governance_context"]["evidence_refs"] == ["ev-ok"]
    assert [event[0] for event in audit_events] == [
        "workflow_runtime_operations_command_denied",
        "workflow_runtime_operations_command_submitted",
    ]


def test_runtime_command_idempotency_namespace_preserves_identity_boundaries():
    read_models = WorkflowRuntimeReadModelService(InMemoryWorkflowRuntimeReadModelRepository())
    for tenant_id, run_id in (("a:b", "c"), ("a", "b:c")):
        read_models.record_snapshot(
            _snapshot(
                run_id,
                tenant_id=tenant_id,
                evidence=[{"evidence_id": "ev-ok", "kind": "acceptance", "status": "verified"}],
                gates=[
                    {
                        "gate_id": "gate-ops",
                        "status": "approved",
                        "approval_id": "approval-1",
                        "required_evidence_refs": ["ev-ok"],
                        "allowed_commands": ["cancel_run"],
                    }
                ],
            )
        )
    gateway = _RecordingGateway()
    service = WorkflowRuntimeCommandService(read_models=read_models, gateway=gateway)
    request = RuntimeOperationCommandRequest.from_mapping(
        {
            "type": "cancel_run",
            "approval_id": "approval-1",
            "evidence_refs": ["ev-ok"],
        },
        idempotency_key="same-client-key",
    )

    service.dispatch(tenant_id="a:b", run_id="c", actor="operator", request=request)
    service.dispatch(tenant_id="a", run_id="b:c", actor="operator", request=request)

    assert gateway.calls[0]["idempotency_key"] != gateway.calls[1]["idempotency_key"]


def test_runtime_operations_idempotency_conflict_is_409_without_second_hub_side_effect(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    read_models = get_workflow_runtime_read_model_service()
    read_models.record_snapshot(
        _snapshot(
            "run-idempotency-conflict",
            evidence=[{"evidence_id": "ev-ok", "kind": "acceptance", "status": "verified"}],
            gates=[
                {
                    "gate_id": "gate-ops",
                    "status": "approved",
                    "approval_id": "approval-1",
                    "required_evidence_refs": ["ev-ok"],
                    "allowed_commands": ["pause_run", "cancel_run"],
                }
            ],
        )
    )
    interventions: list[tuple[str, str, str]] = []

    class _TaskAdmin:
        @staticmethod
        def intervene_task(*, task_id: str, action: str, actor: str):
            interventions.append((task_id, action, actor))
            return True, "ok", {"id": task_id, "status": "paused"}

    class _CoreServices:
        task_admin_service = _TaskAdmin()

    monkeypatch.setattr(
        "agent.services.service_registry.get_core_services",
        lambda: _CoreServices(),
    )
    run_control = RunControlService()
    command_service = WorkflowRuntimeCommandService(
        read_models=read_models,
        gateway=RunControlRuntimeCommandGateway(service_provider=lambda: run_control),
    )
    monkeypatch.setattr(
        "agent.routes.workflow_runtime_operations.get_workflow_runtime_command_service",
        lambda: command_service,
    )
    headers = {
        **_headers(tenant_id="tenant-a"),
        "Idempotency-Key": "runtime-idempotency-key-1",
    }
    base_payload = {
        "type": "pause_run",
        "approval_id": "approval-1",
        "evidence_refs": ["ev-ok"],
    }
    endpoint = "/api/workflow-runtime/operations/runs/run-idempotency-conflict/commands"

    accepted = client.post(endpoint, headers=headers, json=base_payload)
    replayed = client.post(endpoint, headers=headers, json=base_payload)
    conflict = client.post(
        endpoint,
        headers=headers,
        json={**base_payload, "type": "cancel_run"},
    )

    assert accepted.status_code == 202
    assert replayed.status_code == 202
    assert (
        accepted.get_json()["command"]["command_id"]
        == replayed.get_json()["command"]["command_id"]
    )
    assert conflict.status_code == 409
    assert conflict.get_json()["reason_code"] == "runtime_command_idempotency_conflict"
    assert interventions == [("task-run-idempotency-conflict", "pause", "operator-a")]
    assert len(run_control._commands) == 1


def test_runtime_command_rejects_non_array_evidence_and_oversized_payload(client):
    headers = {**_headers(tenant_id="tenant-a"), "Idempotency-Key": "command-shape-001"}
    invalid_evidence = client.post(
        "/api/workflow-runtime/operations/runs/run-1/commands",
        headers=headers,
        json={"type": "cancel_run", "approval_id": "approval-1", "evidence_refs": "ev-1"},
    )
    assert invalid_evidence.status_code == 422
    assert invalid_evidence.get_json()["reason_code"] == "runtime_command_verified_evidence_required"

    oversized = client.post(
        "/api/workflow-runtime/operations/runs/run-1/commands",
        headers={**headers, "Content-Type": "application/json"},
        data='{"type":"cancel_run","padding":"' + ("x" * (17 * 1024)) + '"}',
    )
    assert oversized.status_code == 413
    assert oversized.get_json()["reason_code"] == "runtime_command_payload_too_large"


@pytest.mark.parametrize(
    ("payload", "header", "reason_code", "status_code"),
    [
        (
            {"type": "cancel_run", "approval_id": "a" * 161, "evidence_refs": ["ev-1"]},
            "command-shape-001",
            "runtime_command_approval_required",
            422,
        ),
        (
            {"type": "cancel_run", "approval_id": "approval-1", "evidence_refs": [" ev-1"]},
            "command-shape-001",
            "runtime_command_verified_evidence_required",
            422,
        ),
        (
            {
                "type": "cancel_run",
                "approval_id": "approval-1",
                "evidence_refs": ["ev-1"],
                "idempotency_key": " command-shape-001",
            },
            "",
            "runtime_command_idempotency_key_required",
            400,
        ),
    ],
)
def test_runtime_command_rejects_noncanonical_binding_identities(
    client,
    payload: dict[str, Any],
    header: str,
    reason_code: str,
    status_code: int,
):
    response = client.post(
        "/api/workflow-runtime/operations/runs/run-1/commands",
        headers={**_headers(tenant_id="tenant-a"), "Idempotency-Key": header},
        json=payload,
    )

    assert response.status_code == status_code
    assert response.get_json()["reason_code"] == reason_code


@pytest.mark.parametrize(
    ("field_name", "record_value", "reason_code"),
    [
        ("approval_id", "a" * 161, "approval_id_too_long"),
        ("approval_id", " approval-1", "approval_id_not_canonical"),
        ("required_evidence_refs", ["e" * 161], "evidence_ref_too_long"),
    ],
)
def test_read_model_rejects_ambiguous_gate_binding_identities(
    field_name: str,
    record_value: Any,
    reason_code: str,
):
    gate = {"gate_id": "gate-1", "status": "approved", "approval_id": "approval-1"}
    gate[field_name] = record_value
    snapshot = _snapshot("run-gate-identity", gates=[gate])

    with pytest.raises(ValueError, match=reason_code):
        WorkflowRuntimeOperationRecord.from_mapping(snapshot)


def test_runtime_command_is_fail_closed_for_stale_read_model(client, monkeypatch: pytest.MonkeyPatch):
    read_models = get_workflow_runtime_read_model_service()
    read_models.record_snapshot(
        _snapshot(
            "run-stale-command",
            updated_at=time.time() - 90,
            evidence=[{"evidence_id": "ev-ok", "kind": "probe", "status": "verified"}],
            gates=[
                {
                    "gate_id": "gate-ops",
                    "status": "approved",
                    "approval_id": "approval-1",
                    "required_evidence_refs": ["ev-ok"],
                }
            ],
        )
    )
    gateway = _RecordingGateway()
    monkeypatch.setattr(
        "agent.routes.workflow_runtime_operations.get_workflow_runtime_command_service",
        lambda: WorkflowRuntimeCommandService(read_models=read_models, gateway=gateway),
    )
    response = client.post(
        "/api/workflow-runtime/operations/runs/run-stale-command/commands",
        headers={**_headers(tenant_id="tenant-a"), "Idempotency-Key": "stale-command-001"},
        json={
            "type": "cancel_run",
            "approval_id": "approval-1",
            "evidence_refs": ["ev-ok"],
        },
    )
    assert response.status_code == 409
    assert response.get_json()["reason_code"] == "runtime_read_model_stale"
    assert gateway.calls == []


def test_runtime_operation_route_preserves_hub_policy_409(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "recovery_source_cancel_requires_hub_control"

    class RejectedCommandService:
        @staticmethod
        def dispatch(**_values: Any) -> dict[str, Any]:
            return {
                "command_id": "command-recovery-source-cancel",
                "type": "cancel_run",
                "status": "rejected_by_policy",
                "result": {
                    "error": reason,
                    "reason_code": reason,
                    "http_status": 409,
                },
            }

    monkeypatch.setattr(
        (
            "agent.routes.workflow_runtime_operations."
            "get_workflow_runtime_command_service"
        ),
        lambda: RejectedCommandService(),
    )

    response = client.post(
        (
            "/api/workflow-runtime/operations/runs/"
            "run-recovery-source/commands"
        ),
        headers={
            **_headers(tenant_id="tenant-a"),
            "Idempotency-Key": "source-cancel-command-001",
        },
        json={
            "type": "cancel_run",
            "approval_id": "approval-source-cancel",
            "evidence_refs": ["evidence-source-cancel"],
        },
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["reason_code"] == (
        "runtime_command_rejected_by_hub_policy"
    )
    assert payload["command"]["result"]["reason_code"] == reason
