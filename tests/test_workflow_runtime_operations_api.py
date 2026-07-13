from __future__ import annotations

import time
from typing import Any, Mapping

import pytest

from agent.auth import generate_token
from agent.config import settings
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent
from agent.services.workflow_runtime_command_service import WorkflowRuntimeCommandService
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
        {"role": "admin"},
        settings.secret_key,
    )

    response = client.get(
        "/api/workflow-runtime/operations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.get_json()["reason_code"] == "workflow_runtime_identity_required"


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
    get_workflow_runtime_read_model_service().record_snapshot(
        _snapshot("run-private", tenant_id="tenant-a")
    )
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
            event(2, "workflow.evidence.recorded", {
                "evidence_id": "ev-events",
                "kind": "acceptance",
                "status": "verified",
                "summary": "Hub verified",
            }),
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
    assert call["idempotency_key"].startswith("runtime-ops:tenant-a:run-command:")
    assert call["governance_context"]["evidence_refs"] == ["ev-ok"]
    assert [event[0] for event in audit_events] == [
        "workflow_runtime_operations_command_denied",
        "workflow_runtime_operations_command_submitted",
    ]


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
