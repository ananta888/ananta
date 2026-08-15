from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_command_transition_admission import (
    WorkflowCommandTransitionAdmissionService,
)
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_control_command_receipts import WorkflowControlCommandReceipt
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.events import InMemoryEventStore
from agent.services.workflow_runtime.ownership import InMemoryExecutionOwnershipStore
from agent.services.workflow_runtime.security import HmacKeyRing
from agent.services.workflow_transition_effect_execution import (
    BoundedWorkflowTransitionRetryPolicy,
    FinalizationObserved,
    FinalizationQuarantine,
    FinalizationRetry,
)
from agent.services.workflow_transition_native_composition import (
    NATIVE_COMMAND_EVENT_TYPE,
    NativeBindingFinalizationObserver,
    NativeCommandTransitionIntentFactory,
    NativeTransitionPublicProjector,
    WorkflowTransitionDriver,
    WorkflowTransitionNativeCompositionError,
    build_native_transition_effect_registry,
    build_native_transition_finalization_registry,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_BINDING_FINALIZE,
    EFFECT_EVENT_APPEND,
    EFFECT_OWNERSHIP_RESERVE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_COMPLETED,
    WorkflowTransitionError,
    workflow_transition_request_fingerprint,
)
from agent.services.workflow_transition_persistence import InMemoryWorkflowTransitionStore
from agent.services.workflow_transition_runner import (
    RUN_OUTCOME_COMPLETED,
    RUN_OUTCOME_PROGRESSED,
    WorkflowTransitionRunner,
)

ROOT = Path(__file__).resolve().parents[1]

_NOW = 1_000.0


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


def _command() -> SignedWorkflowCommand:
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
        payload={"value": 1},
        now=_NOW,
        command_id="command-a",
        nonce="nonce-a",
    )


def _receipt() -> WorkflowControlCommandReceipt:
    request = {
        "actor_roles": ["operator"],
        "admitted_command": _command().to_dict(),
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


class _StatusReads:
    """Authoritative status stub standing in for the Native backend."""

    def __init__(self, status: dict[str, Any] | None = None) -> None:
        self.status = (
            status
            if status is not None
            else {
                "status": "running",
                "revision": 8,
                "checkpoint_ref": "checkpoint-8",
            }
        )
        self.calls: list[str] = []
        self.error: Exception | None = None

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        self.calls.append(workflow_id)
        if self.error is not None:
            raise self.error
        return dict(self.status)


def _store() -> InMemoryWorkflowTransitionStore:
    store = InMemoryWorkflowTransitionStore(
        clock=lambda: _NOW,
        receipt_projector=NativeTransitionPublicProjector(),
    )
    store.put_binding(
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        runtime_id=TRANSITION_RUNTIME_NATIVE,
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
        request_payload=_receipt().request_payload,
    )
    return store


@pytest.fixture()
def harness() -> dict[str, Any]:
    events = InMemoryEventStore()
    ownership = InMemoryExecutionOwnershipStore()
    store = _store()
    status_reads = _StatusReads()
    factory = NativeCommandTransitionIntentFactory(events=events, transitions=store)
    admission = WorkflowCommandTransitionAdmissionService(
        store,
        transition_reader=store,
        intent_factory=factory,
        clock=lambda: _NOW,
    )
    runner = WorkflowTransitionRunner(
        reads=store,
        leases=store,
        effects=store,
        completion=store,
        quarantine=store,
        effect_registry=build_native_transition_effect_registry(
            ownership_authority=ownership,
            ownership_reads=ownership,
            event_authority=events,
            event_reads=events,
            clock=lambda: _NOW,
        ),
        finalization_registry=build_native_transition_finalization_registry(
            status_reads=status_reads,
        ),
        retry_policy=BoundedWorkflowTransitionRetryPolicy(3, 2.0, 2.0, 10.0),
        owner_id="runner-a",
        lease_seconds=30.0,
        clock=lambda: _NOW,
    )
    return {
        "admission": admission,
        "events": events,
        "ownership": ownership,
        "runner": runner,
        "status_reads": status_reads,
        "store": store,
    }


def _run_to_completion(runner: Any, transition_id: str, *, maximum: int = 8) -> Any:
    """Drive one transition to a terminal state; each run advances one effect."""

    result = runner.run(transition_id)
    for _ in range(maximum):
        if result.outcome != RUN_OUTCOME_PROGRESSED:
            return result
        result = runner.run(transition_id)
    raise AssertionError("transition did not terminate within the bounded step budget")


def test_planner_produces_the_exact_admissible_three_effect_command_plan(
    harness: dict[str, Any],
) -> None:
    snapshot = harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())

    transition = snapshot.transition
    assert transition.kind == TRANSITION_KIND_COMMAND
    assert transition.runtime_id == TRANSITION_RUNTIME_NATIVE
    assert transition.command_id == "command-a"
    assert transition.expected_revision == 7
    assert transition.expected_checkpoint_ref == "checkpoint-7"
    assert tuple(effect.kind for effect in snapshot.effects) == (
        EFFECT_OWNERSHIP_RESERVE,
        EFFECT_EVENT_APPEND,
        EFFECT_BINDING_FINALIZE,
    )
    assert tuple(effect.ordinal for effect in snapshot.effects) == (1, 2, 3)


def test_command_transition_runs_end_to_end_and_appends_one_visible_event(
    harness: dict[str, Any],
) -> None:
    staged = harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())

    result = _run_to_completion(harness["runner"], staged.transition.transition_id)

    assert result.outcome == RUN_OUTCOME_COMPLETED
    assert result.snapshot.transition.state == TRANSITION_STATE_COMPLETED
    appended = harness["events"].list_events(tenant_id="tenant-a", run_id="run-a")
    assert len(appended) == 1
    assert appended[0].event_type == NATIVE_COMMAND_EVENT_TYPE
    assert harness["status_reads"].calls == ["workflow-a"]


def test_rerunning_the_same_transition_appends_no_second_event(
    harness: dict[str, Any],
) -> None:
    staged = harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())
    _run_to_completion(harness["runner"], staged.transition.transition_id)

    repeated = harness["runner"].run(staged.transition.transition_id)

    assert repeated.snapshot.transition.state == TRANSITION_STATE_COMPLETED
    assert len(harness["events"].list_events(tenant_id="tenant-a", run_id="run-a")) == 1


def test_readmitting_the_same_command_adopts_the_same_transition(
    harness: dict[str, Any],
) -> None:
    first = harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())

    second = harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())

    assert second.transition.transition_id == first.transition.transition_id
    assert second.transition.effect_fingerprint == first.transition.effect_fingerprint


def test_readmitting_an_in_flight_transition_fails_on_state_not_on_drift(
    harness: dict[str, Any],
) -> None:
    """An in-flight command is recovered by receipt lease, never by replanning.

    The adoption-first planner returns the persisted plan verbatim, so the
    store rejects this on transition state alone.  Were the planner to replan,
    the moved event head would yield a different effect fingerprint for the
    same command and the failure would look like an attribution conflict.
    """

    staged = harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())
    harness["runner"].run(staged.transition.transition_id)
    harness["runner"].run(staged.transition.transition_id)

    with pytest.raises(WorkflowTransitionError, match="stage_state_invalid"):
        harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())

    assert len(harness["events"].list_events(tenant_id="tenant-a", run_id="run-a")) == 1
    assert harness["store"].get(staged.transition.transition_id) is not None


def test_planner_rejects_a_receipt_without_an_admitted_command(
    harness: dict[str, Any],
) -> None:
    receipt = _receipt()
    broken = WorkflowControlCommandReceipt(
        command_id=receipt.command_id,
        tenant_id=receipt.tenant_id,
        workflow_id=receipt.workflow_id,
        run_id=receipt.run_id,
        actor_id=receipt.actor_id,
        command_type=receipt.command_type,
        request_payload={"payload": {"value": 1}},
        expected_revision=receipt.expected_revision,
        checkpoint_ref=receipt.checkpoint_ref,
        request_fingerprint=workflow_transition_request_fingerprint({"payload": {"value": 1}}),
    )

    with pytest.raises(WorkflowTransitionNativeCompositionError, match="admitted_command_invalid"):
        harness["admission"].stage_or_adopt(receipt=broken, binding=_binding())


def test_finalization_retries_instead_of_finalizing_a_behind_revision(
    harness: dict[str, Any],
) -> None:
    harness["status_reads"].status = {
        "status": "running",
        "revision": 3,
        "checkpoint_ref": "checkpoint-3",
    }
    staged = harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())

    result = _run_to_completion(harness["runner"], staged.transition.transition_id)

    assert result.snapshot.transition.state != TRANSITION_STATE_COMPLETED
    assert len(harness["events"].list_events(tenant_id="tenant-a", run_id="run-a")) == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        ({"status": "running", "revision": True, "checkpoint_ref": "c-1"}, FinalizationQuarantine),
        ({"status": "running", "revision": -1, "checkpoint_ref": "c-1"}, FinalizationQuarantine),
        ({"status": "running", "revision": 8, "checkpoint_ref": ""}, FinalizationQuarantine),
        ({"status": "running", "revision": 8, "checkpoint_ref": "checkpoint-8"}, FinalizationObserved),
        ({"status": "running", "revision": 3, "checkpoint_ref": "checkpoint-3"}, FinalizationRetry),
    ),
)
def test_finalization_observer_classifies_authoritative_status(
    status: dict[str, Any],
    expected: type,
) -> None:
    observer = NativeBindingFinalizationObserver(status_reads=_StatusReads(status))

    observed = observer.observe(_StubAttempt(), heartbeat=_NullHeartbeat())

    assert isinstance(observed, expected)


def test_finalization_reports_an_unavailable_backend_as_retryable() -> None:
    reads = _StatusReads()
    reads.error = TimeoutError("backend down")
    observer = NativeBindingFinalizationObserver(status_reads=reads)

    result = observer.observe(_StubAttempt(), heartbeat=_NullHeartbeat())

    assert isinstance(result, FinalizationRetry)
    assert result.reason_code == "native_binding_status_unavailable"


def test_driver_reports_processed_transitions_and_bounds_its_batch(
    harness: dict[str, Any],
) -> None:
    harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())
    driver = WorkflowTransitionDriver(runner=harness["runner"], limit=8)

    first = driver.tick()
    second = driver.tick()

    # One bounded tick re-claims the same transition until it terminates, so a
    # three-effect plan needs three claims and no second tick.
    assert first.processed == 3
    assert first.outcomes[-1] == RUN_OUTCOME_COMPLETED
    assert first.to_dict()["runtime_id"] == TRANSITION_RUNTIME_NATIVE
    assert second.processed == 0


def test_driver_tick_is_a_noop_without_due_transitions(harness: dict[str, Any]) -> None:
    driver = WorkflowTransitionDriver(runner=harness["runner"])

    report = driver.tick()

    assert report.processed == 0
    assert report.outcomes == ()


@pytest.mark.parametrize("limit", (0, -1, 257, True, 1.5))
def test_driver_rejects_an_unbounded_or_non_integer_limit(
    harness: dict[str, Any],
    limit: Any,
) -> None:
    with pytest.raises(WorkflowTransitionNativeCompositionError, match="limit_invalid"):
        WorkflowTransitionDriver(runner=harness["runner"], limit=limit)


def test_registry_resolves_only_the_two_adapters_that_exist(
    harness: dict[str, Any],
) -> None:
    registry = build_native_transition_effect_registry(
        ownership_authority=harness["ownership"],
        ownership_reads=harness["ownership"],
        event_authority=harness["events"],
        event_reads=harness["events"],
        clock=lambda: _NOW,
    )

    assert registry.resolve(runtime_id=TRANSITION_RUNTIME_NATIVE, effect_kind=EFFECT_OWNERSHIP_RESERVE)
    assert registry.resolve(runtime_id=TRANSITION_RUNTIME_NATIVE, effect_kind=EFFECT_EVENT_APPEND)
    with pytest.raises(Exception, match="executor_missing"):
        registry.resolve(runtime_id=TRANSITION_RUNTIME_NATIVE, effect_kind="queue_reserve")


def test_planner_rejects_an_invalid_lease_or_retry_budget() -> None:
    events = InMemoryEventStore()
    with pytest.raises(WorkflowTransitionNativeCompositionError, match="lease_invalid"):
        NativeCommandTransitionIntentFactory(events=events, transitions=_store(), lease_seconds=0.0)
    with pytest.raises(WorkflowTransitionNativeCompositionError, match="retries_invalid"):
        NativeCommandTransitionIntentFactory(events=events, transitions=_store(), maximum_retries=0)


class _NullHeartbeat:
    @property
    def claim_generation(self) -> int:
        return 1

    def heartbeat(self) -> None:
        return None


class _StubTransition:
    workflow_id = "workflow-a"
    expected_revision = 7
    transition_id = "transition-a"


class _StubSnapshot:
    transition = _StubTransition()


class _StubAttempt:
    """Minimal attempt shape the observer reads without runner coupling."""

    snapshot = _StubSnapshot()
    claim_generation = 1
