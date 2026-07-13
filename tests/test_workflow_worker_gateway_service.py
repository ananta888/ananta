from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.services.workflow_authorization_grant_service import (
    InMemoryWorkflowAuthorizationGrantService,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    HmacKeyRing,
    InMemoryEventStore,
    InMemoryExecutionOwnershipStore,
    InMemoryProviderBudgetStore,
    InMemoryReplayNonceStore,
    InMemorySideEffectLedger,
    RuntimeAuthorizationEnvelope,
)
from agent.services.workflow_worker_gateway_service import (
    WorkflowToolApprovalDecision,
    WorkflowToolDescriptor,
    WorkflowWorkerGatewayError,
    WorkflowWorkerGatewayService,
)
from ananta_contracts.workflow_operation import operation_id_for
from ananta_contracts.workflow_worker_gateway import WORKFLOW_WORKER_COMMAND_SCHEMA

PLAN_HASH = "a" * 64


class _DigestBoundApprovals:
    def __init__(self) -> None:
        self.consumed: list[str] = []

    def authorize(
        self,
        *,
        approval_ref: str,
        tool_id: str,
        arguments: dict,
        hub_task_id: str,
        goal_id: str | None,
    ) -> WorkflowToolApprovalDecision:
        if (
            approval_ref == "approval-1"
            and tool_id == "apply_patch"
            and arguments == {"patch_artifact_id": "patch-1"}
            and hub_task_id == "hub-task-1"
            and goal_id == "goal-1"
        ):
            return WorkflowToolApprovalDecision(
                True,
                "workflow_tool_approval_granted",
                approval_id=approval_ref,
            )
        return WorkflowToolApprovalDecision(
            False,
            "workflow_tool_approval_binding_mismatch",
        )

    def consume(self, approval_ref: str) -> bool:
        self.consumed.append(approval_ref)
        return approval_ref == "approval-1"


class _HubToolDescriptors:
    _CLASSES = {
        "apply_patch": "idempotent_write",
        "non-idempotent-tool": "non_idempotent_write",
        "read-tool": "read",
        "run_shell": "idempotent_write",
    }

    def resolve(self, tool_id: str) -> WorkflowToolDescriptor | None:
        side_effect_class = self._CLASSES.get(tool_id)
        if side_effect_class is None:
            return None
        return WorkflowToolDescriptor(tool_id, side_effect_class)


def fixture() -> tuple[
    WorkflowWorkerGatewayService,
    dict,
    InMemoryEventStore,
    InMemorySideEffectLedger,
    _DigestBoundApprovals,
]:
    now = time.time()
    key_ring = HmacKeyRing({"key-1": b"x" * 32}, active_key_id="key-1")
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=key_ring,
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash=PLAN_HASH,
        policy_version="policy-v1",
        allowed_tools=("apply_patch",),
        budgets={
            "attempts": 2,
            "cost_micros": 1_000,
            "retries": 3,
            "tokens": 100,
        },
        now=now,
        ttl_seconds=600,
    )
    ownership = InMemoryExecutionOwnershipStore()
    claim = ownership.claim(
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        owner_id="worker-1",
        lease_seconds=300,
        maximum_retries=3,
        now=now,
    )
    events = InMemoryEventStore()
    ledger = InMemorySideEffectLedger()
    grants = InMemoryWorkflowAuthorizationGrantService(clock=lambda: now + 1)
    grants.grant(envelope)
    approvals = _DigestBoundApprovals()
    service = WorkflowWorkerGatewayService(
        authorization=AuthorizationVerifier(key_ring, InMemoryReplayNonceStore()),
        ownership=ownership,
        ledger=ledger,
        events=events,
        provider_budgets=InMemoryProviderBudgetStore(),
        authorization_revalidator=grants,
        tool_approvals=approvals,
        tool_descriptors=_HubToolDescriptors(),
        clock=lambda: now + 1,
    )
    binding = {
        "tenant_id": "tenant-1",
        "workflow_id": "workflow-1",
        "run_id": "run-1",
        "step_id": "step-1",
        "plan_hash": PLAN_HASH,
        "policy_version": "policy-v1",
        "authorization_envelope": envelope.to_dict(),
        "correlation_id": "correlation-1",
    }
    base = {
        "schema": WORKFLOW_WORKER_COMMAND_SCHEMA,
        "binding": binding,
        "attempt_id": claim.ownership.attempt_id,
        "fencing_token": claim.ownership.fencing_token,
    }
    return service, base, events, ledger, approvals


def _tool_command(base: dict, command: str = "side_effect_claim") -> dict:
    return {
        **base,
        "command": command,
        "tool_id": "apply_patch",
        "side_effect_class": "idempotent_write",
        "approval_ref": "approval-1",
        "hub_task_id": "hub-task-1",
        "goal_id": "goal-1",
        "arguments": {"patch_artifact_id": "patch-1"},
        "operation_id": operation_id_for(
            tenant_id="tenant-1",
            run_id="run-1",
            step_id="step-1",
            declared_operation="tool:apply_patch",
        ),
    }


def test_hub_authorizes_and_completes_one_fenced_tool_side_effect() -> None:
    service, base, events, _ledger, approvals = fixture()

    authorized = service.execute(_tool_command(base, "authorize_tool"))
    claimed = service.execute(_tool_command(base))
    duplicate = service.execute(_tool_command(base))
    completed = service.execute(
        {
            **_tool_command(base, "side_effect_complete"),
            "expected_revision": claimed["record"]["revision"],
            "result_ref": "artifact-result-1",
        }
    )

    assert authorized["allowed"] is True
    assert claimed["acquired"] is True
    assert duplicate["acquired"] is False
    assert duplicate["reason"] == "already_claimed"
    assert completed["record"]["status"] == "completed"
    assert completed["approval_consumed"] is True
    assert approvals.consumed == ["approval-1"]
    assert {event.event_type for event in events.list_events(tenant_id="tenant-1", run_id="run-1")} >= {
        "workflow.tool.authorization_checked",
        "workflow.tool.approval_consumed",
        "workflow.side_effect.authorized",
        "workflow.side_effect.started",
        "workflow.side_effect.completed",
    }


def test_write_tool_approval_is_digest_and_task_bound_before_ledger_claim() -> None:
    service, base, _events, ledger, approvals = fixture()
    operation_id = _tool_command(base)["operation_id"]

    denied = service.execute(
        {
            **_tool_command(base, "authorize_tool"),
            "arguments": {"patch_artifact_id": "different-patch"},
        }
    )
    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="workflow_tool_approval_binding_mismatch",
    ):
        service.execute(
            {
                **_tool_command(base),
                "hub_task_id": "different-task",
            }
        )

    assert denied["allowed"] is False
    assert denied["reason_code"] == "workflow_tool_approval_binding_mismatch"
    assert ledger.get(tenant_id="tenant-1", operation_id=operation_id) is None
    assert approvals.consumed == []


def test_write_tool_without_approval_binding_fails_closed() -> None:
    service, base, _events, ledger, _approvals = fixture()
    command = _tool_command(base, "authorize_tool")
    command.pop("approval_ref")

    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="workflow_tool_approval_required",
    ):
        service.execute(command)

    assert (
        ledger.get(
            tenant_id="tenant-1",
            operation_id=_tool_command(base)["operation_id"],
        )
        is None
    )


@pytest.mark.parametrize("reported_class", ["read", "none", None])
def test_worker_cannot_downgrade_non_idempotent_side_effect_class(
    reported_class: str | None,
) -> None:
    service, base, _events, ledger, _approvals = fixture()
    command = {
        **_tool_command(base),
        "tool_id": "non-idempotent-tool",
        "side_effect_class": reported_class,
        "approval_ref": "",
        "hub_task_id": "",
        "goal_id": "",
        "arguments": {},
        "operation_id": operation_id_for(
            tenant_id="tenant-1",
            run_id="run-1",
            step_id="step-1",
            declared_operation="tool:non-idempotent-tool",
        ),
    }
    if reported_class is None:
        command.pop("side_effect_class")

    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="workflow_tool_side_effect_class_mismatch",
    ):
        service.execute(command)

    assert (
        ledger.get(
            tenant_id="tenant-1",
            operation_id=command["operation_id"],
        )
        is None
    )


def test_unknown_tool_operation_is_rejected_before_ledger_claim() -> None:
    service, base, _events, ledger, _approvals = fixture()
    operation_id = operation_id_for(
        tenant_id="tenant-1",
        run_id="run-1",
        step_id="step-1",
        declared_operation="tool:unknown-operation",
    )

    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="workflow_tool_descriptor_unknown",
    ):
        service.execute(
            {
                **_tool_command(base),
                "tool_id": "unknown-operation",
                "side_effect_class": "non_idempotent_write",
                "operation_id": operation_id,
            }
        )

    assert ledger.get(tenant_id="tenant-1", operation_id=operation_id) is None


def test_provider_budget_reservation_and_reconciliation_are_hub_owned() -> None:
    service, base, events, _ledger, _approvals = fixture()
    command = {
        **base,
        "command": "provider_budget_reserve",
        "reservation_id": "provider-call-1",
        "maximum_attempts": 2,
        "maximum_tokens": 100,
        "maximum_cost_micros": 1_000,
        "reserved_tokens": 40,
        "reserved_cost_micros": 300,
    }
    reserved = service.execute(command)
    duplicate = service.execute(command)
    reconciled = service.execute(
        {
            **base,
            "command": "provider_budget_reconcile",
            "reservation_id": "provider-call-1",
            "actual_total_tokens": 25,
        }
    )

    assert reserved == duplicate
    assert reserved["attempts"] == 1
    assert reconciled["tokens"] == 25
    assert reconciled["reconciled"] is True
    assert {
        event.event_type
        for event in events.list_events(tenant_id="tenant-1", run_id="run-1")
    } >= {
        "workflow.budget.provider_reserved",
        "workflow.budget.provider_reconciled",
    }


def test_provider_budget_cannot_exceed_signed_envelope_or_stale_fence() -> None:
    service, base, _events, _ledger, _approvals = fixture()
    with pytest.raises(WorkflowWorkerGatewayError, match="authorization_budget_exceeded"):
        service.execute(
            {
                **base,
                "command": "provider_budget_reserve",
                "reservation_id": "provider-call-over-budget",
                "maximum_attempts": 2,
                "maximum_tokens": 101,
                "maximum_cost_micros": 1_000,
                "reserved_tokens": 1,
                "reserved_cost_micros": 1,
            }
        )
    with pytest.raises(WorkflowWorkerGatewayError, match="fencing_mismatch"):
        service.execute(
            {
                **base,
                "command": "provider_budget_reserve",
                "reservation_id": "provider-call-stale",
                "maximum_attempts": 2,
                "maximum_tokens": 100,
                "maximum_cost_micros": 1_000,
                "reserved_tokens": 1,
                "reserved_cost_micros": 1,
                "fencing_token": 999,
            }
        )


def test_worker_retry_uses_same_hub_owned_budget_and_is_idempotent() -> None:
    service, base, events, _ledger, _approvals = fixture()
    command = {
        **base,
        "command": "consume_retry",
        "retry_id": "provider-retry-1",
        "retry_category": "provider",
        "maximum": 3,
    }

    first = service.execute(command)
    duplicate = service.execute(command)
    second_category = service.execute(
        {
            **command,
            "retry_id": "tool-retry-1",
            "retry_category": "tool",
        }
    )

    assert first["used"] == duplicate["used"] == 1
    assert second_category["used"] == 2
    budget_events = [
        event
        for event in events.list_events(tenant_id="tenant-1", run_id="run-1")
        if event.event_type == "workflow.budget.retry_consumed"
    ]
    assert len(budget_events) == 2


def test_stale_owner_and_scope_escalation_fail_before_a_side_effect() -> None:
    service, base, _events, _ledger, _approvals = fixture()

    with pytest.raises(WorkflowWorkerGatewayError, match="workflow_worker_fencing_mismatch"):
        service.execute({**_tool_command(base), "fencing_token": 999})

    escalated = {
        **_tool_command(base),
        "tool_id": "run_shell",
        "operation_id": operation_id_for(
            tenant_id="tenant-1",
            run_id="run-1",
            step_id="step-1",
            declared_operation="tool:run_shell",
        ),
    }
    with pytest.raises(WorkflowWorkerGatewayError, match="authorization_tool_denied"):
        service.execute(escalated)


def test_parallel_duplicate_delivery_has_one_exactly_once_decision() -> None:
    service, base, _events, _ledger, _approvals = fixture()
    command = _tool_command(base)

    def run() -> tuple[str, bool]:
        try:
            receipt = service.execute(command)
            return receipt["reason"], bool(receipt["acquired"])
        except WorkflowWorkerGatewayError as exc:
            return exc.reason_code, False

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(lambda _index: run(), range(16)))

    assert sum(1 for _reason, acquired in decisions if acquired) == 1
    assert all(
        acquired
        or reason in {"already_claimed", "side_effect_compare_and_set_failed"}
        for reason, acquired in decisions
    )


def test_native_worker_advances_only_the_pre_authorized_hub_operation() -> None:
    service, base, events, ledger, _approvals = fixture()
    record = ledger.plan(
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        declared_operation="write-project-tree",
        side_effect_class="idempotent_write",
    )
    record = ledger.authorize(
        record.operation_id,
        expected_revision=record.revision,
        fencing_token=base["fencing_token"],
        authorization_envelope_id=base["binding"]["authorization_envelope"][
            "envelope_id"
        ],
    )
    claim_command = {
        **base,
        "command": "native_side_effect_claim",
        "operation_id": record.operation_id,
        "expected_revision": record.revision,
    }

    claimed = service.execute(claim_command)
    duplicate = service.execute(
        {**claim_command, "expected_revision": claimed["record"]["revision"]}
    )
    completed = service.execute(
        {
            **base,
            "command": "native_side_effect_complete",
            "operation_id": record.operation_id,
            "expected_revision": claimed["record"]["revision"],
            "result_ref": "artifact-native-result-1",
        }
    )

    assert claimed["acquired"] is True
    assert duplicate["acquired"] is False
    assert duplicate["reason"] == "already_claimed"
    assert completed["record"]["status"] == "completed"
    assert {
        event.event_type
        for event in events.list_events(tenant_id="tenant-1", run_id="run-1")
    } >= {"workflow.side_effect.started", "workflow.side_effect.completed"}


def test_native_worker_cannot_plan_or_rebind_a_side_effect() -> None:
    service, base, _events, ledger, _approvals = fixture()
    unplanned_operation = operation_id_for(
        tenant_id="tenant-1",
        run_id="run-1",
        step_id="step-1",
        declared_operation="not-planned-by-hub",
    )
    with pytest.raises(WorkflowWorkerGatewayError, match="side_effect_operation_not_found"):
        service.execute(
            {
                **base,
                "command": "native_side_effect_claim",
                "operation_id": unplanned_operation,
                "expected_revision": 2,
            }
        )

    record = ledger.plan(
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        declared_operation="bound-operation",
        side_effect_class="non_idempotent_write",
    )
    record = ledger.authorize(
        record.operation_id,
        expected_revision=record.revision,
        fencing_token=base["fencing_token"],
        authorization_envelope_id="different-envelope",
    )
    with pytest.raises(
        WorkflowWorkerGatewayError, match="side_effect_operation_binding_mismatch"
    ):
        service.execute(
            {
                **base,
                "command": "native_side_effect_claim",
                "operation_id": record.operation_id,
                "expected_revision": record.revision,
            }
        )
