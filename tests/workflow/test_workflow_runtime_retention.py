from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.workflow_runtime import (
    CanonicalWorkflowEvent,
    ContractValidationError,
    WorkflowEventRetentionPolicy,
    WorkflowEventRetentionService,
)


def _event(sequence: int, *, tenant_id: str = "tenant-a") -> CanonicalWorkflowEvent:
    return CanonicalWorkflowEvent.build(
        tenant_id=tenant_id,
        workflow_id="workflow-1",
        run_id="run-1",
        event_type="workflow.step.completed",
        correlation_id="correlation-1",
        causation_id=f"cause-{sequence}",
        dedupe_key=f"dedupe-{sequence}",
        step_id="step-1",
        payload={"api_key": "must-never-survive", "result_ref": f"artifact://{sequence}"},
        occurred_at=100.0 + sequence,
        event_id=f"event-{sequence}",
    ).with_sequence(sequence)


def test_retention_attestation_is_payload_free_redacted_and_byte_stable() -> None:
    service = WorkflowEventRetentionService(
        WorkflowEventRetentionPolicy(policy_version="retention-v1")
    )
    events = (_event(1), _event(2))

    first = service.attest(
        tenant_id="tenant-a", run_id="run-1", events=events, evaluated_at=1_000_000.0
    )
    second = service.attest(
        tenant_id="tenant-a", run_id="run-1", events=events, evaluated_at=1_000_000.0
    )

    assert first.to_dict() == second.to_dict()
    assert "must-never-survive" not in str(events)
    assert "must-never-survive" not in str(first.to_dict())
    assert first.event_count == 2 and first.last_sequence == 2
    service.verify(first, events=events, evaluated_at=1_000_000.0)


def test_retention_rejects_partial_history_cross_tenant_and_tampering() -> None:
    service = WorkflowEventRetentionService(
        WorkflowEventRetentionPolicy(policy_version="retention-v1")
    )
    with pytest.raises(ContractValidationError, match="sequence_gap"):
        service.attest(
            tenant_id="tenant-a", run_id="run-1", events=(_event(2),), evaluated_at=1000
        )
    with pytest.raises(ContractValidationError, match="binding_mismatch"):
        service.attest(
            tenant_id="tenant-a",
            run_id="run-1",
            events=(_event(1, tenant_id="tenant-b"),),
            evaluated_at=1000,
        )

    valid = service.attest(
        tenant_id="tenant-a", run_id="run-1", events=(_event(1),), evaluated_at=1000
    )
    tampered = replace(valid, content_chain_hash="0" * 64)
    with pytest.raises(ContractValidationError, match="attestation_mismatch"):
        service.verify(tampered, events=(_event(1),), evaluated_at=1000)


def test_canonical_event_purge_mode_is_never_a_valid_runtime_policy() -> None:
    with pytest.raises(ContractValidationError, match="canonical_event_purge_forbidden"):
        WorkflowEventRetentionPolicy(
            policy_version="unsafe-v1", mode="delete_after_ttl"
        ).assert_valid()
