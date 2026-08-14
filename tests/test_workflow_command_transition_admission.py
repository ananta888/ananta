from __future__ import annotations

from dataclasses import replace
from inspect import Parameter, signature
from pathlib import Path
from typing import Any

import pytest

from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_command_transition_admission import (
    WorkflowCommandTransitionAdmissionError,
    WorkflowCommandTransitionAdmissionService,
    WorkflowCommandTransitionIntent,
)
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_control_command_receipts import (
    COMMAND_RECEIPT_COMPLETED,
    WorkflowControlCommandReceipt,
    WorkflowControlCommandReceiptError,
    WorkflowControlCommandReceiptReconciler,
)
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.security import HmacKeyRing
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_QUEUE_RESERVE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_LANGGRAPH,
    TRANSITION_RUNTIME_NATIVE,
    WorkflowTransition,
    WorkflowTransitionEffect,
    workflow_admitted_command_digest,
    workflow_transition_id,
    workflow_transition_request_fingerprint,
)
from agent.services.workflow_transition_persistence import (
    InMemoryWorkflowTransitionStore,
    WorkflowTransitionPersistenceError,
)

ROOT = Path(__file__).resolve().parents[1]


def _binding(*, runtime_id: str = "local") -> WorkflowControlRunBinding:
    return WorkflowControlRunBinding(
        tenant_id="tenant-a",
        subject_id="subject-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        plan_hash="f" * 64,
        policy_version="policy-v1",
        checkpoint_id="checkpoint-7",
        request=WorkflowRequest.from_mapping(
            {
                "workflow_id": "workflow-a",
                "correlation_id": "correlation-a",
                "requested_by": "subject-a",
                "steps": [],
            }
        ),
    )


def _command(
    *,
    payload: dict[str, Any] | None = None,
    nonce: str = "nonce-a",
    now: float = 1_000.0,
) -> SignedWorkflowCommand:
    return SignedWorkflowCommand.issue(
        key_ring=HmacKeyRing({"test-key": b"x" * 32}, active_key_id="test-key"),
        command_type="pause",
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        checkpoint_id="checkpoint-7",
        expected_revision=7,
        plan_hash="f" * 64,
        policy_version="policy-v1",
        actor_id="subject-a",
        actor_roles=("operator",),
        payload=payload or {"value": 1},
        now=now,
        command_id="command-a",
        nonce=nonce,
    )


def _receipt(*, request_payload: dict[str, Any] | None = None) -> WorkflowControlCommandReceipt:
    command = _command()
    request = request_payload or {
        "actor_roles": ["operator"],
        "admitted_command": command.to_dict(),
        "payload": {"value": 1},
        "step_id": "step-a",
    }
    return WorkflowControlCommandReceipt(
        command_id="command-a",
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        actor_id="subject-a",
        command_type="pause",
        request_payload=request,
        expected_revision=7,
        checkpoint_ref="checkpoint-7",
        request_fingerprint=workflow_transition_request_fingerprint(request),
    )


class _NativeTestIntentFactory:
    """Complete test-only plan; production has no generic default planner."""

    def __init__(self, runtime_id: str = TRANSITION_RUNTIME_NATIVE) -> None:
        self._runtime_id = runtime_id

    def build(
        self,
        *,
        receipt: WorkflowControlCommandReceipt,
        binding: WorkflowControlRunBinding,
    ) -> WorkflowCommandTransitionIntent:
        transition_id = workflow_transition_id(
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            runtime_id=self._runtime_id,
            kind=TRANSITION_KIND_COMMAND,
            identity_key=receipt.command_id,
        )
        effects = (
            WorkflowTransitionEffect.build(
                transition_id=transition_id,
                ordinal=1,
                kind=EFFECT_QUEUE_RESERVE,
                idempotency_key="task-a",
                payload={"task_id": "task-a", "command_id": receipt.command_id},
                created_at=1_000.0,
            ),
            WorkflowTransitionEffect.build(
                transition_id=transition_id,
                ordinal=2,
                kind=EFFECT_BINDING_FINALIZE,
                idempotency_key=binding.workflow_id,
                payload={"workflow_id": binding.workflow_id},
                created_at=1_000.0,
            ),
        )
        admitted = receipt.request_payload["admitted_command"]
        assert isinstance(admitted, dict)
        transition = WorkflowTransition.build(
            transition_id=transition_id,
            tenant_id=binding.tenant_id,
            workflow_id=binding.workflow_id,
            run_id=binding.run_id,
            runtime_id=self._runtime_id,
            kind=TRANSITION_KIND_COMMAND,
            command_id=receipt.command_id,
            receipt_id=receipt.command_id,
            admitted_command=admitted,
            request_payload=receipt.request_payload,
            effects=effects,
            expected_revision=receipt.expected_revision,
            expected_checkpoint_ref=receipt.checkpoint_ref,
            created_at=1_000.0,
        )
        return WorkflowCommandTransitionIntent(transition, effects)


def _store(
    receipt: WorkflowControlCommandReceipt,
    *,
    runtime_id: str = "local",
) -> InMemoryWorkflowTransitionStore:
    store = InMemoryWorkflowTransitionStore(clock=lambda: 1_000.0)
    store.put_binding(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=runtime_id,
        runtime_revision=7,
        runtime_checkpoint_ref="checkpoint-7",
        command_receipt_id="command-a",
    )
    store.put_receipt(
        receipt_id="command-a",
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        expected_revision=7,
        checkpoint_ref="checkpoint-7",
        request_payload=receipt.request_payload,
    )
    return store


def test_admission_stages_and_exactly_adopts_an_injected_complete_plan() -> None:
    receipt = _receipt()
    store = _store(receipt)
    service = WorkflowCommandTransitionAdmissionService(
        store,
        intent_factory=_NativeTestIntentFactory(),
    )

    staged = service.stage_or_adopt(receipt=receipt, binding=_binding())
    attributed = replace(
        receipt,
        transition_id=staged.transition.transition_id,
        effect_fingerprint=staged.transition.effect_fingerprint,
    )
    adopted = service.stage_or_adopt(receipt=attributed, binding=_binding())

    assert adopted == staged
    assert store.active_transition_id("workflow-a") == staged.transition.transition_id
    assert store.receipt_record("command-a")["transition_id"] == staged.transition.transition_id


@pytest.mark.parametrize(
    ("binding_runtime", "transition_runtime"),
    [
        ("local", TRANSITION_RUNTIME_NATIVE),
        (TRANSITION_RUNTIME_LANGGRAPH, TRANSITION_RUNTIME_LANGGRAPH),
    ],
)
def test_admission_exactly_matches_native_alias_and_langgraph_runtime(
    binding_runtime: str,
    transition_runtime: str,
) -> None:
    receipt = _receipt()
    store = _store(receipt, runtime_id=binding_runtime)
    service = WorkflowCommandTransitionAdmissionService(
        store,
        intent_factory=_NativeTestIntentFactory(transition_runtime),
    )

    staged = service.stage_or_adopt(
        receipt=receipt,
        binding=_binding(runtime_id=binding_runtime),
    )

    assert staged.transition.runtime_id == transition_runtime


def test_admission_rejects_a_binding_transition_runtime_mismatch() -> None:
    receipt = _receipt()
    service = WorkflowCommandTransitionAdmissionService(
        _store(receipt),
        intent_factory=_NativeTestIntentFactory(TRANSITION_RUNTIME_LANGGRAPH),
    )

    with pytest.raises(WorkflowCommandTransitionAdmissionError, match="intent_mismatch"):
        service.stage_or_adopt(receipt=receipt, binding=_binding(runtime_id="local"))


def test_pre_cutover_admission_has_no_live_composition_or_default_plan() -> None:
    production_cutover_files = (
        "agent/services/workflow_control_composition.py",
        "agent/services/workflow_control_production_composition.py",
        "agent/services/workflow_control_command_receipts.py",
        "agent/services/native_graph_control_bridge.py",
        "agent/services/langgraph_workflow_control_bridge.py",
    )
    live_only_symbols = (
        "WorkflowCommandTransitionAdmissionService",
        "SQLAlchemyWorkflowTransitionStore",
        "WorkflowTransitionPublicStatusProjector",
    )
    for relative_path in production_cutover_files:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert all(symbol not in source for symbol in live_only_symbols), relative_path

    intent_factory = signature(WorkflowCommandTransitionAdmissionService).parameters[
        "intent_factory"
    ]
    assert intent_factory.default is Parameter.empty


def test_admission_rejects_attribution_and_request_divergence() -> None:
    receipt = _receipt()
    store = _store(receipt)
    service = WorkflowCommandTransitionAdmissionService(
        store,
        intent_factory=_NativeTestIntentFactory(),
    )
    staged = service.stage_or_adopt(receipt=receipt, binding=_binding())

    with pytest.raises(WorkflowCommandTransitionAdmissionError, match="id_conflict"):
        service.stage_or_adopt(
            receipt=replace(
                receipt,
                transition_id="different-transition",
                effect_fingerprint=staged.transition.effect_fingerprint,
            ),
            binding=_binding(),
        )
    divergent_request = {
        **receipt.request_payload,
        "payload": {"value": 2},
    }
    divergent = replace(
        receipt,
        request_payload=divergent_request,
        request_fingerprint=workflow_transition_request_fingerprint(divergent_request),
    )
    with pytest.raises(WorkflowTransitionPersistenceError, match="stage_conflict"):
        service.stage_or_adopt(receipt=divergent, binding=_binding())
    assert store.get(staged.transition.transition_id) == staged


def test_admitted_command_digest_ignores_only_renewable_authority() -> None:
    first = _command().to_dict()
    renewed = {
        **first,
        "schema": "ananta.workflow-command.v2",
        "issued_at": 1_100.0,
        "expires_at": 1_400.0,
        "nonce": "nonce-b",
        "key_id": "rotated-key",
        "signature": "renewed-signature",
        "signature_algorithm": "rotated-algorithm",
        "payload_digest": "0" * 64,
    }
    assert workflow_admitted_command_digest(first) == workflow_admitted_command_digest(renewed)

    for field_name, value in (
        ("expected_revision", 8),
        ("payload", {"value": 2}),
        ("actor_roles", ["auditor"]),
    ):
        divergent = {**renewed, field_name: value}
        assert workflow_admitted_command_digest(first) != workflow_admitted_command_digest(divergent)


def test_transition_adoption_binds_the_original_receipt_authority_envelope() -> None:
    receipt = _receipt()
    store = _store(receipt)
    service = WorkflowCommandTransitionAdmissionService(
        store,
        intent_factory=_NativeTestIntentFactory(),
    )
    staged = service.stage_or_adopt(receipt=receipt, binding=_binding())
    renewed_command = _command(nonce="nonce-b", now=1_001.0)
    renewed_request = {
        **receipt.request_payload,
        "admitted_command": renewed_command.to_dict(),
    }
    renewed_receipt = replace(
        receipt,
        request_payload=renewed_request,
        request_fingerprint=workflow_transition_request_fingerprint(renewed_request),
    )

    assert workflow_admitted_command_digest(
        receipt.request_payload["admitted_command"]
    ) == workflow_admitted_command_digest(renewed_command.to_dict())
    with pytest.raises(WorkflowTransitionPersistenceError, match="stage_conflict"):
        service.stage_or_adopt(receipt=renewed_receipt, binding=_binding())
    assert store.get(staged.transition.transition_id) == staged


def test_attributed_completed_receipt_requires_an_outcome_fingerprint() -> None:
    legacy_completed = replace(
        _receipt(),
        state=COMMAND_RECEIPT_COMPLETED,
        result_status={"revision": 8},
    )
    assert legacy_completed.outcome_fingerprint == ""

    with pytest.raises(
        WorkflowControlCommandReceiptError,
        match="outcome_invalid",
    ):
        replace(
            legacy_completed,
            transition_id="transition-a",
            effect_fingerprint="e" * 64,
        )

    attributed = replace(
        legacy_completed,
        transition_id="transition-a",
        effect_fingerprint="e" * 64,
        outcome_fingerprint="a" * 64,
    )
    assert attributed.outcome_fingerprint == "a" * 64


def test_legacy_reconciler_never_adopts_an_attributed_receipt_by_revision() -> None:
    attributed = replace(
        _receipt(),
        transition_id="transition-a",
        effect_fingerprint="e" * 64,
    )

    class _LeakyReceiptStore:
        claimed = False

        def list_pending(self, *, limit: int = 100):
            assert limit == 1_000
            return (attributed,)

        def claim(self, *args: Any, **kwargs: Any):
            self.claimed = True
            pytest.fail("an attributed receipt reached the legacy claim path")

    class _AdvancedBindings:
        @staticmethod
        def last_status(_workflow_id: str) -> dict[str, Any]:
            return {"revision": 99, "status": "completed"}

        @staticmethod
        def get(_workflow_id: str) -> WorkflowControlRunBinding:
            return _binding()

    receipts = _LeakyReceiptStore()
    reconciler = WorkflowControlCommandReceiptReconciler(
        receipts=receipts,  # type: ignore[arg-type] - deliberate leaky boundary fake
        bindings=_AdvancedBindings(),
        project=lambda _binding, status: status,
        recover=lambda _receipt, _binding: pytest.fail("legacy recovery was called"),
        owner_id="legacy-owner",
    )

    with pytest.raises(
        WorkflowControlCommandReceiptError,
        match="workflow_control_command_transition_pending",
    ):
        reconciler.reconcile_workflow("workflow-a")
    assert receipts.claimed is False
