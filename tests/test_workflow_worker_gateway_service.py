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
from agent.services.workflow_worker_assignment_service import (
    InMemoryWorkflowWorkerAssignmentStore,
    WorkflowWorkerAssignment,
)
from agent.services.workflow_worker_gateway_service import (
    WorkflowToolApprovalDecision,
    WorkflowToolDescriptor,
    WorkflowWorkerGatewayError,
    WorkflowWorkerGatewayService,
)
from ananta_contracts.provider_execution import (
    ProviderExecutionBinding,
    ProviderProfileAttemptPlanEntry,
    ProviderProfileExecutionBinding,
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


def fixture(
    *,
    provider_attempts: int | None = None,
    provider_attempt_plan: tuple[
        ProviderProfileAttemptPlanEntry, ...
    ] = (),
    budget_overrides: dict[str, int] | None = None,
) -> tuple[
    WorkflowWorkerGatewayService,
    dict,
    InMemoryEventStore,
    InMemorySideEffectLedger,
    _DigestBoundApprovals,
]:
    now = time.time()
    key_ring = HmacKeyRing({"key-1": b"x" * 32}, active_key_id="key-1")
    budgets = {
        "attempts": 2,
        "cost_micros": 1_000,
        "retries": 3,
        "tokens": 100,
    }
    if provider_attempts is not None:
        budgets["provider_attempts"] = provider_attempts
    budgets.update(budget_overrides or {})
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=key_ring,
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        plan_hash=PLAN_HASH,
        policy_version="policy-v1",
        allowed_tools=("apply_patch",),
        allowed_provider_bindings=tuple(
            item.binding_authorization
            for item in provider_attempt_plan
        ),
        provider_attempt_plan=provider_attempt_plan,
        budgets=budgets,
        now=now,
        ttl_seconds=600,
    )
    ownership = InMemoryExecutionOwnershipStore()
    claim = ownership.claim(
        tenant_id="tenant-1",
        workflow_id="workflow-1",
        run_id="run-1",
        step_id="step-1",
        owner_id="hub-native:run-1:step-1",
        lease_seconds=300,
        maximum_retries=3,
        now=now,
    )
    events = InMemoryEventStore()
    ledger = InMemorySideEffectLedger()
    grants = InMemoryWorkflowAuthorizationGrantService(clock=lambda: now + 1)
    grants.grant(envelope)
    approvals = _DigestBoundApprovals()
    assignments = InMemoryWorkflowWorkerAssignmentStore()
    assignments.bind(
        WorkflowWorkerAssignment(
            tenant_id="tenant-1",
            workflow_id="workflow-1",
            run_id="run-1",
            step_id="step-1",
            attempt_id=claim.ownership.attempt_id,
            fencing_token=claim.ownership.fencing_token,
            hub_task_id="hub-task-1",
            worker_id="worker-1",
            worker_url="http://worker-1:5000",
            assigned_at=now,
        )
    )
    service = WorkflowWorkerGatewayService(
        authorization=AuthorizationVerifier(key_ring, InMemoryReplayNonceStore()),
        ownership=ownership,
        ledger=ledger,
        events=events,
        provider_budgets=InMemoryProviderBudgetStore(),
        authorization_revalidator=grants,
        tool_approvals=approvals,
        tool_descriptors=_HubToolDescriptors(),
        assignments=assignments,
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


def _provider_profile_plan(
    *,
    phi_attempts: int = 3,
    gemma_attempts: int = 2,
) -> tuple[
    tuple[ProviderProfileExecutionBinding, ...],
    tuple[ProviderProfileAttemptPlanEntry, ...],
]:
    bindings = tuple(
        ProviderProfileExecutionBinding(
            profile_id=profile_id,
            binding=ProviderExecutionBinding(
                provider_id="ollama",
                model_id=model_id,
                source="hub_model_profile_routing",
                reason_code="hub_provider_profile_selected",
                endpoint_identity=(
                    "http://ollama:11434/v1/chat/completions"
                ),
            ),
        )
        for profile_id, model_id in (
            ("phi-primary", "phi4-mini:latest"),
            ("gemma-fallback", "gemma4:e4b-it-qat"),
        )
    )
    attempts = (phi_attempts, gemma_attempts)
    return bindings, tuple(
        ProviderProfileAttemptPlanEntry.from_profile_binding(
            binding,
            maximum_attempts=maximum,
        )
        for binding, maximum in zip(
            bindings,
            attempts,
            strict=True,
        )
    )


def _profile_provider_reserve(
    base: dict,
    entry: ProviderProfileAttemptPlanEntry,
    *,
    reservation_id: str,
    maximum_attempts: int,
    reserved_tokens: int = 1,
    reserved_cost_micros: int = 0,
) -> dict:
    return {
        **base,
        "command": "provider_budget_reserve",
        "reservation_id": reservation_id,
        "maximum_attempts": maximum_attempts,
        "maximum_tokens": 100,
        "maximum_cost_micros": 1_000,
        "reserved_tokens": reserved_tokens,
        "reserved_cost_micros": reserved_cost_micros,
        "provider_profile_id": entry.profile_id,
        "provider_binding_id": entry.binding_id,
        "provider_id": entry.provider_id,
        "model_id": entry.model_id,
        "provider_endpoint_identity": entry.endpoint_identity,
    }


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


def test_authenticated_worker_identity_must_own_the_exact_active_lease() -> None:
    service, base, _events, _ledger, _approvals = fixture()
    command = {
        **base,
        "command": "authorize_execution",
        "adapter_kind": "native",
    }

    accepted = service.execute(
        command,
        authenticated_worker_id="worker-1",
        authenticated_worker_url="http://worker-1:5000",
    )
    assert accepted["allowed"] is True

    with pytest.raises(WorkflowWorkerGatewayError) as foreign:
        service.execute(
            command,
            authenticated_worker_id="worker-2",
            authenticated_worker_url="http://worker-2:5000",
        )
    assert foreign.value.status_code == 403
    assert foreign.value.reason_code == (
        "workflow_worker_authenticated_owner_mismatch"
    )

    with pytest.raises(WorkflowWorkerGatewayError) as incomplete:
        service.execute(
            command,
            authenticated_worker_id="worker-1",
        )
    assert incomplete.value.status_code == 403
    assert incomplete.value.reason_code == (
        "workflow_worker_authenticated_identity_invalid"
    )


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


def test_provider_reservation_actuals_cannot_bypass_smaller_signed_node_cap() -> None:
    service, base, _events, _ledger, _approvals = fixture(
        budget_overrides={
            "tokens": 30,
            "cost_micros": 300,
            "provider_run_tokens": 100,
            "provider_run_cost_micros": 1_000,
        }
    )

    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="authorization_budget_exceeded",
    ):
        service.execute(
            {
                **base,
                "command": "provider_budget_reserve",
                "reservation_id": "provider-node-cap-bypass",
                "maximum_attempts": 2,
                # Zero means that the Worker does not add its own cap. The
                # signed node cap must still govern the actual reservation.
                "maximum_tokens": 0,
                "maximum_cost_micros": 0,
                "reserved_tokens": 80,
                "reserved_cost_micros": 800,
            }
        )


def test_provider_node_cap_is_cumulative_replay_safe_and_atomic() -> None:
    service, base, _events, _ledger, _approvals = fixture(
        budget_overrides={
            "tokens": 30,
            "cost_micros": 300,
            "provider_run_tokens": 100,
            "provider_run_cost_micros": 1_000,
        }
    )

    def command(index: int) -> dict:
        return {
            **base,
            "command": "provider_budget_reserve",
            "reservation_id": f"provider-node-split-{index}",
            "maximum_attempts": 2,
            "maximum_tokens": 30,
            "maximum_cost_micros": 300,
            "reserved_tokens": 20,
            "reserved_cost_micros": 150,
        }

    def reserve(index: int):
        try:
            return index, service.execute(command(index))
        except WorkflowWorkerGatewayError as exc:
            return index, exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, range(2)))

    successes = [
        (index, value)
        for index, value in results
        if isinstance(value, dict)
    ]
    failures = [
        value
        for _index, value in results
        if isinstance(value, WorkflowWorkerGatewayError)
    ]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].reason_code == "provider_token_budget_exceeded"

    winner_index, winner = successes[0]
    assert service.execute(command(winner_index)) == winner
    final = service.execute(
        {
            **base,
            "command": "provider_budget_reserve",
            "reservation_id": "provider-node-split-final",
            "maximum_attempts": 2,
            "maximum_tokens": 30,
            "maximum_cost_micros": 300,
            "reserved_tokens": 10,
            "reserved_cost_micros": 150,
        }
    )
    assert final["attempts"] == 2
    assert final["tokens"] == 30
    assert final["cost_micros"] == 300


def test_provider_node_actual_overrun_is_visible_in_receipt_and_event() -> None:
    service, base, events, _ledger, _approvals = fixture(
        budget_overrides={
            "tokens": 30,
            "cost_micros": 300,
            "provider_run_tokens": 100,
            "provider_run_cost_micros": 1_000,
        }
    )
    service.execute(
        {
            **base,
            "command": "provider_budget_reserve",
            "reservation_id": "provider-node-overrun",
            "maximum_attempts": 2,
            "maximum_tokens": 30,
            "maximum_cost_micros": 300,
            "reserved_tokens": 20,
            "reserved_cost_micros": 150,
        }
    )
    receipt = service.execute(
        {
            **base,
            "command": "provider_budget_reconcile",
            "reservation_id": "provider-node-overrun",
            "actual_total_tokens": 50,
        }
    )

    assert receipt["tokens"] == 50
    assert receipt["scoped_tokens"] == 50
    assert receipt["scoped_budget_overrun"] is True
    assert receipt["reason_code"] == (
        "provider_scoped_budget_overrun_recorded"
    )
    reconciled_event = next(
        event
        for event in events.list_events(
            tenant_id="tenant-1",
            run_id="run-1",
        )
        if event.event_type == "workflow.budget.provider_reconciled"
    )
    assert reconciled_event.payload["scoped_budget_overrun"] is True
    assert reconciled_event.payload["reason_code"] == (
        "provider_scoped_budget_overrun_recorded"
    )


def test_failed_aggregate_reservation_does_not_burn_profile_or_unlock_fallback() -> None:
    _bindings, plan = _provider_profile_plan(
        phi_attempts=1,
        gemma_attempts=1,
    )
    service, base, _events, _ledger, _approvals = fixture(
        provider_attempts=2,
        provider_attempt_plan=plan,
        budget_overrides={
            "tokens": 100,
            "provider_run_tokens": 2,
        },
    )
    phi, gemma = plan

    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="provider_token_budget_exceeded",
    ):
        service.execute(
            _profile_provider_reserve(
                base,
                phi,
                reservation_id="phi-aggregate-denied",
                maximum_attempts=2,
                reserved_tokens=3,
            )
        )
    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="provider_attempt_plan_sequence_denied",
    ):
        service.execute(
            _profile_provider_reserve(
                base,
                gemma,
                reservation_id="gemma-still-locked",
                maximum_attempts=2,
            )
        )

    phi_receipt = service.execute(
        _profile_provider_reserve(
            base,
            phi,
            reservation_id="phi-valid",
            maximum_attempts=2,
        )
    )
    gemma_receipt = service.execute(
        _profile_provider_reserve(
            base,
            gemma,
            reservation_id="gemma-after-valid-phi",
            maximum_attempts=2,
        )
    )

    assert phi_receipt["attempts"] == 1
    assert gemma_receipt["attempts"] == 1
    assert gemma_receipt["tokens"] == 2


def test_concurrent_profile_reservations_consume_one_atomic_slot() -> None:
    _bindings, plan = _provider_profile_plan(
        phi_attempts=1,
        gemma_attempts=1,
    )
    service, base, _events, _ledger, _approvals = fixture(
        provider_attempts=2,
        provider_attempt_plan=plan,
    )
    phi, gemma = plan
    commands = tuple(
        _profile_provider_reserve(
            base,
            phi,
            reservation_id=f"phi-concurrent-{index}",
            maximum_attempts=2,
        )
        for index in range(2)
    )

    def reserve(command: dict):
        try:
            return service.execute(command)
        except WorkflowWorkerGatewayError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, commands))

    receipts = [item for item in results if isinstance(item, dict)]
    errors = [
        item
        for item in results
        if isinstance(item, WorkflowWorkerGatewayError)
    ]
    assert len(receipts) == 1
    assert len(errors) == 1
    assert errors[0].reason_code == "provider_retry_budget_exceeded"
    assert receipts[0]["attempts"] == 1
    assert receipts[0]["tokens"] == 1

    fallback = service.execute(
        _profile_provider_reserve(
            base,
            gemma,
            reservation_id="gemma-after-concurrent-phi",
            maximum_attempts=2,
        )
    )
    assert fallback["attempts"] == 1
    assert fallback["tokens"] == 2


def test_profile_route_has_separate_five_call_budget_and_rejects_sixth_or_inflation() -> None:
    service, base, _events, _ledger, _approvals = fixture(
        provider_attempts=5
    )
    for attempt in range(5):
        reserved = service.execute(
            {
                **base,
                "command": "provider_budget_reserve",
                "reservation_id": f"profile-provider-call-{attempt}",
                "maximum_attempts": 5,
                "require_separate_provider_attempt_budget": False,
                "maximum_tokens": 100,
                "maximum_cost_micros": 1_000,
                "reserved_tokens": 1,
                "reserved_cost_micros": 0,
            }
        )
        assert reserved["attempts"] == attempt + 1

    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="provider_retry_budget_exceeded",
    ):
        service.execute(
            {
                **base,
                "command": "provider_budget_reserve",
                "reservation_id": "profile-provider-call-six",
                "maximum_attempts": 5,
                "require_separate_provider_attempt_budget": False,
                "maximum_tokens": 100,
                "maximum_cost_micros": 1_000,
                "reserved_tokens": 1,
                "reserved_cost_micros": 0,
            }
        )

    fresh, fresh_base, _events, _ledger, _approvals = fixture(
        provider_attempts=5
    )
    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="authorization_budget_exceeded",
    ):
        fresh.execute(
            {
                **fresh_base,
                "command": "provider_budget_reserve",
                "reservation_id": "profile-provider-call-inflated",
                "maximum_attempts": 6,
                "require_separate_provider_attempt_budget": False,
                "maximum_tokens": 100,
                "maximum_cost_micros": 1_000,
                "reserved_tokens": 1,
                "reserved_cost_micros": 0,
            }
        )


def test_signed_profile_plan_enforces_bindings_caps_idempotency_and_hub_limit() -> None:
    _bindings, plan = _provider_profile_plan()
    service, base, _events, _ledger, _approvals = fixture(
        provider_attempts=5,
        provider_attempt_plan=plan,
    )
    phi, gemma = plan

    first_command = _profile_provider_reserve(
        base,
        phi,
        reservation_id="signed-phi-1",
        maximum_attempts=5,
    )
    first = service.execute(first_command)
    duplicate = service.execute(first_command)
    assert first == duplicate
    assert first["attempts"] == 1
    assert first["maximum_attempts"] == 3

    for index in (2, 3):
        receipt = service.execute(
            _profile_provider_reserve(
                base,
                phi,
                reservation_id=f"signed-phi-{index}",
                maximum_attempts=5,
            )
        )
        assert receipt["attempts"] == index

    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="provider_retry_budget_exceeded",
    ):
        service.execute(
            _profile_provider_reserve(
                base,
                phi,
                reservation_id="signed-phi-4",
                maximum_attempts=5,
            )
        )

    for index in (1, 2):
        receipt = service.execute(
            _profile_provider_reserve(
                base,
                gemma,
                reservation_id=f"signed-gemma-{index}",
                maximum_attempts=5,
            )
        )
        assert receipt["attempts"] == index
        assert receipt["maximum_attempts"] == 2

    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="authorization_budget_exceeded",
    ):
        service.execute(
            _profile_provider_reserve(
                base,
                gemma,
                reservation_id="signed-limit-inflated",
                maximum_attempts=6,
            )
        )


def test_signed_profile_plan_denies_fallback_before_predecessor_is_exhausted() -> None:
    _bindings, plan = _provider_profile_plan()
    service, base, _events, _ledger, _approvals = fixture(
        provider_attempts=5,
        provider_attempt_plan=plan,
    )
    phi, gemma = plan

    for reservation_id in ("gemma-first",):
        with pytest.raises(
            WorkflowWorkerGatewayError,
            match="provider_attempt_plan_sequence_denied",
        ):
            service.execute(
                _profile_provider_reserve(
                    base,
                    gemma,
                    reservation_id=reservation_id,
                    maximum_attempts=5,
                )
            )

    service.execute(
        _profile_provider_reserve(
            base,
            phi,
            reservation_id="phi-first",
            maximum_attempts=5,
        )
    )
    with pytest.raises(
        WorkflowWorkerGatewayError,
        match="provider_attempt_plan_sequence_denied",
    ):
        service.execute(
            _profile_provider_reserve(
                base,
                gemma,
                reservation_id="gemma-interleaved",
                maximum_attempts=5,
            )
        )

    for index in (2, 3):
        service.execute(
            _profile_provider_reserve(
                base,
                phi,
                reservation_id=f"phi-{index}",
                maximum_attempts=5,
            )
        )
    fallback = service.execute(
        _profile_provider_reserve(
            base,
            gemma,
            reservation_id="gemma-after-phi-exhaustion",
            maximum_attempts=5,
        )
    )

    assert fallback["attempts"] == 1
    assert fallback["maximum_attempts"] == 2


def test_signed_provider_binding_rejects_model_provider_and_recomputed_id() -> None:
    _bindings, plan = _provider_profile_plan()
    service, base, _events, _ledger, _approvals = fixture(
        provider_attempts=5,
        provider_attempt_plan=plan,
    )
    phi = plan[0]
    recomputed = ProviderExecutionBinding(
        provider_id="ollama",
        model_id="attacker-model:latest",
        source="hub_model_profile_routing",
        reason_code="hub_provider_profile_selected",
    )
    tampered_values = (
        {"model_id": "attacker-model:latest"},
        {"provider_id": "openai"},
        {
            "provider_endpoint_identity": (
                "http://ollama:11435/v1/chat/completions"
            )
        },
        {
            "binding_id": recomputed.binding_id,
            "provider_id": recomputed.provider_id,
            "model_id": recomputed.model_id,
        },
    )
    for index, changes in enumerate(tampered_values):
        command = _profile_provider_reserve(
            base,
            phi,
            reservation_id=f"tampered-provider-{index}",
            maximum_attempts=5,
        )
        with pytest.raises(
            WorkflowWorkerGatewayError,
            match="provider_authorization_binding_denied",
        ):
            service.execute({**command, **changes})


def test_profile_attempts_are_step_scoped_while_tokens_remain_run_aggregate() -> None:
    now = time.time()
    key_ring = HmacKeyRing(
        {"key-1": b"x" * 32},
        active_key_id="key-1",
    )
    ownership = InMemoryExecutionOwnershipStore()
    provider_budgets = InMemoryProviderBudgetStore()
    grants = InMemoryWorkflowAuthorizationGrantService(
        clock=lambda: now + 1
    )
    service = WorkflowWorkerGatewayService(
        authorization=AuthorizationVerifier(key_ring),
        ownership=ownership,
        ledger=InMemorySideEffectLedger(),
        events=InMemoryEventStore(),
        provider_budgets=provider_budgets,
        authorization_revalidator=grants,
        clock=lambda: now + 1,
    )

    def bind_step(
        step_id: str,
        plan: tuple[ProviderProfileAttemptPlanEntry, ...],
    ) -> dict:
        envelope = RuntimeAuthorizationEnvelope.issue(
            key_ring=key_ring,
            tenant_id="tenant-1",
            workflow_id="workflow-1",
            run_id="shared-run",
            step_id=step_id,
            plan_hash=PLAN_HASH,
            policy_version="policy-v1",
            allowed_provider_bindings=tuple(
                item.binding_authorization for item in plan
            ),
            provider_attempt_plan=plan,
            budgets={
                "attempts": 1,
                "provider_attempts": sum(
                    item.maximum_attempts for item in plan
                ),
                "tokens": 100,
                "cost_micros": 1_000,
            },
            now=now,
            ttl_seconds=600,
        )
        grants.grant(envelope)
        claim = ownership.claim(
            tenant_id="tenant-1",
            workflow_id="workflow-1",
            run_id="shared-run",
            step_id=step_id,
            owner_id=f"hub-native:shared-run:{step_id}",
            lease_seconds=300,
            maximum_retries=0,
            now=now,
        )
        return {
            "schema": WORKFLOW_WORKER_COMMAND_SCHEMA,
            "binding": {
                "tenant_id": "tenant-1",
                "workflow_id": "workflow-1",
                "run_id": "shared-run",
                "step_id": step_id,
                "plan_hash": PLAN_HASH,
                "policy_version": "policy-v1",
                "authorization_envelope": envelope.to_dict(),
            },
            "attempt_id": claim.ownership.attempt_id,
            "fencing_token": claim.ownership.fencing_token,
        }

    _bindings, five_call_plan = _provider_profile_plan()
    _other_bindings, two_call_plan = _provider_profile_plan(
        phi_attempts=1,
        gemma_attempts=1,
    )
    total_reserved = 0
    last_receipt: dict = {}
    for step_id, plan in (
        ("step-a", five_call_plan),
        ("step-b", five_call_plan),
        ("step-c", two_call_plan),
    ):
        base = bind_step(step_id, plan)
        maximum = sum(item.maximum_attempts for item in plan)
        for entry in plan:
            for attempt in range(entry.maximum_attempts):
                last_receipt = service.execute(
                    _profile_provider_reserve(
                        base,
                        entry,
                        reservation_id=(
                            f"{step_id}-{entry.profile_id}-{attempt}"
                        ),
                        maximum_attempts=maximum,
                    )
                )
                total_reserved += 1

    assert total_reserved == 12
    assert last_receipt["tokens"] == 12
    assert last_receipt["maximum_attempts"] == 1


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
