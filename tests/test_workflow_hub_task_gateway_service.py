from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from agent.services.workflow_authorization_grant_service import (
    InMemoryWorkflowAuthorizationGrantService,
)
from agent.services.workflow_hub_task_gateway_service import (
    FernetDispatchPayloadCodec,
    WorkflowHubTaskError,
    WorkflowHubTaskGatewayService,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    HmacKeyRing,
    InMemoryEventStore,
    InMemoryExecutionOwnershipStore,
    InMemoryReplayNonceStore,
    InMemorySideEffectLedger,
    RuntimeAuthorizationEnvelope,
    operation_id_for,
)
from ananta_contracts.hub_task_gateway import HUB_TASK_COMMAND_SCHEMA


class Tasks:
    def __init__(self) -> None:
        self.values = {}

    def get(self, task_id):
        value = self.values.get(task_id)
        return dict(value) if value else None

    def create(self, *, task_id, request, runtime_context):
        self.values[task_id] = {
            "id": task_id,
            "status": "created",
            "worker_execution_context": {"workflow_runtime": runtime_context},
            "verification_status": {},
        }

    def update(self, *, task_id, status, reason_code="", verification_status=None):
        self.values[task_id]["status"] = status
        self.values[task_id]["status_reason_code"] = reason_code
        if verification_status is not None:
            self.values[task_id]["verification_status"] = verification_status


def build_service():
    signing = HmacKeyRing({"key-1": "0123456789abcdef0123456789abcdef"}, active_key_id="key-1")
    tasks = Tasks()
    ledger = InMemorySideEffectLedger()
    ownership = InMemoryExecutionOwnershipStore()
    grants = InMemoryWorkflowAuthorizationGrantService()
    service = WorkflowHubTaskGatewayService(
        tasks=tasks,
        authorization=AuthorizationVerifier(signing, InMemoryReplayNonceStore()),
        ledger=ledger,
        ownership=ownership,
        events=InMemoryEventStore(),
        codec=FernetDispatchPayloadCodec({"enc-1": Fernet.generate_key()}, active_key_id="enc-1"),
        authorization_revalidator=grants,
    )
    return service, signing, tasks, ledger, grants


def command(signing, grants=None, **overrides):
    tenant_id = "tenant-1"
    workflow_id = "workflow-1"
    run_id = "run-1"
    step_id = "step-1"
    plan_hash = "a" * 64
    operation_id = operation_id_for(
        tenant_id=tenant_id,
        run_id=run_id,
        step_id=step_id,
        declared_operation="hub_task",
    )
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=signing,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        plan_hash=plan_hash,
        policy_version="policy-v1",
        budgets={"retries": 2},
        nonce="nonce-1",
        envelope_id="envelope-1",
        now=1_700_000_000,
        ttl_seconds=1_000_000_000,
    )
    if grants is not None:
        grants.grant(envelope)
    payload = {
        "schema": HUB_TASK_COMMAND_SCHEMA,
        "command": "submit",
        "tenant_id": tenant_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "correlation_id": "correlation-1",
        "step_id": step_id,
        "operation_id": operation_id,
        "plan_hash": plan_hash,
        "task_kind": "coding",
        "authorization_envelope": envelope.to_dict(),
        "artifact_refs": [],
        "required_capabilities": ["coding"],
        "activity_class": "idempotent",
        "retry_budget_remaining": 2,
        "parameters": {"declared_operation": "hub_task"},
    }
    payload.update(overrides)
    return payload


def test_submit_revalidates_encrypts_ledgers_and_enqueues() -> None:
    service, signing, tasks, ledger, grants = build_service()

    receipt = service.submit(command(signing, grants))

    assert receipt["status"] == "created"
    assert receipt["authorization_state"] == "valid"
    assert receipt["ledger_state"] == "authorized"
    context = next(iter(tasks.values.values()))["worker_execution_context"]["workflow_runtime"]
    assert "authorization_envelope" not in context
    assert "ciphertext" in context["dispatch"]
    assert ledger.get(tenant_id="tenant-1", operation_id=receipt["operation_id"]) is not None


def test_duplicate_submit_is_idempotent_and_does_not_consume_nonce_twice() -> None:
    service, signing, tasks, _ledger, grants = build_service()
    payload = command(signing, grants)

    first = service.submit(payload)
    second = service.submit(payload)

    assert first == second
    assert len(tasks.values) == 1


def test_finish_requires_attempt_and_fencing_binding() -> None:
    service, signing, tasks, _ledger, grants = build_service()
    receipt = service.submit(command(signing, grants))
    context = next(iter(tasks.values.values()))["worker_execution_context"]["workflow_runtime"]

    completed = service.finish(
        hub_task_id=receipt["hub_task_id"],
        command={
            "operation_id": receipt["operation_id"],
            "status": "completed",
            "attempt_id": context["attempt_id"],
            "fencing_token": context["fencing_token"],
            "result_ref": "artifact-result-1",
            "artifact_refs": [{"artifact_id": "artifact-1"}],
        },
    )

    assert completed["status"] == "completed"
    assert completed["ledger_state"] == "completed"
    assert completed["artifact_refs"] == [{"artifact_id": "artifact-1"}]


def test_uncertain_worker_result_remains_uncertain_in_receipt() -> None:
    service, signing, tasks, _ledger, grants = build_service()
    receipt = service.submit(command(signing, grants))
    context = next(iter(tasks.values.values()))["worker_execution_context"]["workflow_runtime"]

    uncertain = service.finish(
        hub_task_id=receipt["hub_task_id"],
        command={
            "operation_id": receipt["operation_id"],
            "status": "uncertain",
            "attempt_id": context["attempt_id"],
            "fencing_token": context["fencing_token"],
            "reason_code": "worker_connection_lost",
        },
    )

    assert uncertain["status"] == "uncertain"
    assert uncertain["ledger_state"] == "uncertain"
    assert uncertain["reason_code"] == "worker_connection_lost"


def test_dispatch_payload_round_trips_only_through_codec() -> None:
    service, signing, _tasks, _ledger, grants = build_service()
    payload = command(signing, grants)
    receipt = service.submit(payload)

    restored = service.dispatch_payload(
        hub_task_id=receipt["hub_task_id"],
        operation_id=receipt["operation_id"],
    )

    assert restored["authorization_envelope"]["signature"] == payload["authorization_envelope"]["signature"]


def test_combined_retry_budget_is_hub_owned_cross_category_and_idempotent() -> None:
    service, signing, _tasks, _ledger, grants = build_service()
    retry = command(
        signing,
        grants,
        command="consume_retry",
        retry_id="temporal-attempt-2",
        retry_category="temporal_activity",
    )

    first = service.consume_retry(retry)
    duplicate = service.consume_retry(retry)
    second = service.consume_retry(
        {
            **retry,
            "retry_id": "provider-attempt-2",
            "retry_category": "provider",
        }
    )

    assert first == duplicate
    assert first["used"] == 1
    assert second["used"] == 2
    assert second["remaining"] == 0
    with pytest.raises(WorkflowHubTaskError, match="retry_budget_exhausted"):
        service.consume_retry(
            {
                **retry,
                "retry_id": "worker-attempt-2",
                "retry_category": "worker",
            }
        )


def test_retry_budget_rejects_unknown_category_and_tampered_binding() -> None:
    service, signing, _tasks, _ledger, grants = build_service()
    retry = command(
        signing,
        grants,
        command="consume_retry",
        retry_id="retry-1",
        retry_category="nested-runtime",
    )
    with pytest.raises(WorkflowHubTaskError, match="workflow_retry_category_invalid"):
        service.consume_retry(retry)

    with pytest.raises(WorkflowHubTaskError):
        service.consume_retry(
            {
                **retry,
                "retry_category": "tool",
                "tenant_id": "tenant-other",
            }
        )


def test_combined_retry_budget_keeps_a_constant_maximum_when_remaining_decreases() -> None:
    service, signing, _tasks, _ledger, grants = build_service()
    first = command(
        signing,
        grants,
        command="consume_retry",
        retry_id="step-1-attempt-2",
        retry_category="temporal_activity",
        retry_budget_maximum=2,
    )
    second = {
        **first,
        "retry_id": "step-2-attempt-2",
        "retry_budget_remaining": 1,
    }

    assert service.consume_retry(first)["remaining"] == 1
    assert service.consume_retry(second)["remaining"] == 0
