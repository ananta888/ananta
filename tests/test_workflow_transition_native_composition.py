from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_command_transition_admission import (
    WorkflowCommandTransitionAdmissionService,
)
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_control_command_receipts import WorkflowControlCommandReceipt
from agent.services.workflow_runtime.checkpoint_bindings import (
    InMemoryWorkflowTransitionCheckpointBindingStore,
)
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.events import InMemoryEventStore
from agent.services.workflow_runtime.execution_plan import (
    ExecutionBudget,
    ExecutionNode,
    ExecutionPlan,
)
from agent.services.workflow_runtime.ownership import InMemoryExecutionOwnershipStore
from agent.services.workflow_runtime.queue_reservations import (
    InMemoryWorkflowTransitionQueueReservationStore,
)
from agent.services.workflow_runtime.security import HmacKeyRing
from agent.services.workflow_transition_effect_execution import (
    BoundedWorkflowTransitionRetryPolicy,
    FinalizationObserved,
    FinalizationQuarantine,
    FinalizationRetry,
)
from agent.services.workflow_transition_grant_policy import ExecutionPlanGrantPolicy
from agent.services.workflow_transition_native_composition import (
    NATIVE_COMMAND_EVENT_TYPE,
    NativeBindingFinalizationObserver,
    NativeCheckpointBindingWiring,
    NativeCommandTransitionIntentFactory,
    NativeTransitionPublicProjector,
    PlannedAuthorizationGrant,
    WorkflowTransitionDriver,
    WorkflowTransitionNativeCompositionError,
    build_native_transition_effect_registry,
    build_native_transition_finalization_registry,
    workflow_transition_task_id,
)
from agent.services.workflow_transition_outbox import (
    EFFECT_AUTHORIZATION_GRANT,
    EFFECT_BINDING_FINALIZE,
    EFFECT_CHECKPOINT_SAVE,
    EFFECT_EVENT_APPEND,
    EFFECT_OWNERSHIP_RESERVE,
    EFFECT_QUEUE_RESERVE,
    TRANSITION_KIND_COMMAND,
    TRANSITION_RUNTIME_LANGGRAPH,
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
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing

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


def _store(*, runtime_id: str = TRANSITION_RUNTIME_NATIVE) -> InMemoryWorkflowTransitionStore:
    store = InMemoryWorkflowTransitionStore(
        clock=lambda: _NOW,
        receipt_projector=NativeTransitionPublicProjector(),
    )
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
        request_payload=_receipt().request_payload,
    )
    return store


@pytest.fixture()
def harness() -> dict[str, Any]:
    events = InMemoryEventStore()
    ownership = InMemoryExecutionOwnershipStore()
    queue_reservations = InMemoryWorkflowTransitionQueueReservationStore()
    checkpoint_bindings = InMemoryWorkflowTransitionCheckpointBindingStore()
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
            queue_reservations=queue_reservations,
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
        "checkpoint_bindings": checkpoint_bindings,
        "queue_reservations": queue_reservations,
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


def test_a_queue_reserving_plan_runs_end_to_end_and_takes_exactly_one_slot(
    harness: dict[str, Any],
) -> None:
    """The reservation is the ingest: one run, one slot, one event."""

    store = harness["store"]
    admission = WorkflowCommandTransitionAdmissionService(
        store,
        transition_reader=store,
        intent_factory=NativeCommandTransitionIntentFactory(
            events=harness["events"],
            transitions=store,
            reserves_queue_slot=True,
        ),
        clock=lambda: _NOW,
    )
    staged = admission.stage_or_adopt(receipt=_receipt(), binding=_binding())

    result = _run_to_completion(harness["runner"], staged.transition.transition_id)

    assert tuple(effect.kind for effect in staged.effects) == (
        EFFECT_OWNERSHIP_RESERVE,
        EFFECT_QUEUE_RESERVE,
        EFFECT_EVENT_APPEND,
        EFFECT_BINDING_FINALIZE,
    )
    assert result.snapshot.transition.state == TRANSITION_STATE_COMPLETED
    assert len(harness["events"].list_events(tenant_id="tenant-a", run_id="run-a")) == 1
    reserved = harness["queue_reservations"].observe_transition_queue_reservation(
        tenant_id="tenant-a",
        run_id="run-a",
        effect_id=staged.effects[1].effect_id,
    )
    assert reserved.receipt is not None
    assert reserved.receipt.task_id == workflow_transition_task_id(
        transition_id=staged.transition.transition_id
    )


def test_a_command_plan_reserves_no_queue_slot_by_default(harness: dict[str, Any]) -> None:
    """A control command like pause dispatches no work, so it takes no slot."""

    staged = harness["admission"].stage_or_adopt(receipt=_receipt(), binding=_binding())

    assert EFFECT_QUEUE_RESERVE not in {effect.kind for effect in staged.effects}


def _checkpoint_harness(harness: dict[str, Any], checkpoint: Any) -> Any:
    """A runner whose registry also resolves checkpoint_save."""

    return WorkflowTransitionRunner(
        reads=harness["store"],
        leases=harness["store"],
        effects=harness["store"],
        completion=harness["store"],
        quarantine=harness["store"],
        effect_registry=build_native_transition_effect_registry(
            ownership_authority=harness["ownership"],
            ownership_reads=harness["ownership"],
            event_authority=harness["events"],
            event_reads=harness["events"],
            queue_reservations=harness["queue_reservations"],
            checkpoint_bindings=NativeCheckpointBindingWiring(
                authority=harness["checkpoint_bindings"],
                checkpoints=checkpoint,
            ),
            clock=lambda: _NOW,
        ),
        finalization_registry=build_native_transition_finalization_registry(
            status_reads=harness["status_reads"],
        ),
        retry_policy=BoundedWorkflowTransitionRetryPolicy(3, 2.0, 2.0, 10.0),
        owner_id="runner-checkpoint",
        lease_seconds=30.0,
        clock=lambda: _NOW,
    )


class _Checkpoints:
    def __init__(self, revision: int | None) -> None:
        self.revision = revision

    def get_latest(self, *, tenant_id: str, run_id: str, task_id: str) -> Any:
        del tenant_id, run_id, task_id
        if self.revision is None:
            return None
        return SimpleNamespace(
            checkpoint_id=f"checkpoint-{self.revision}",
            revision=self.revision,
            fencing_token=3,
        )


def _checkpoint_plan(harness: dict[str, Any], *, revision: int) -> Any:
    store = harness["store"]
    admission = WorkflowCommandTransitionAdmissionService(
        store,
        transition_reader=store,
        intent_factory=NativeCommandTransitionIntentFactory(
            events=harness["events"],
            transitions=store,
            binds_checkpoint_revision=revision,
        ),
        clock=lambda: _NOW,
    )
    return admission.stage_or_adopt(receipt=_receipt(), binding=_binding())


def test_a_checkpoint_binding_plan_binds_the_exact_revision_the_runtime_wrote(
    harness: dict[str, Any],
) -> None:
    staged = _checkpoint_plan(harness, revision=8)
    runner = _checkpoint_harness(harness, _Checkpoints(8))

    result = _run_to_completion(runner, staged.transition.transition_id)

    assert EFFECT_CHECKPOINT_SAVE in {effect.kind for effect in staged.effects}
    assert result.snapshot.transition.state == TRANSITION_STATE_COMPLETED
    bound = harness["checkpoint_bindings"].observe_transition_checkpoint_binding(
        tenant_id="tenant-a",
        run_id="run-a",
        effect_id=staged.effects[1].effect_id,
    )
    assert bound.receipt is not None
    assert bound.receipt.bound_revision == 8


def test_a_checkpoint_the_runtime_has_not_written_is_a_wait(harness: dict[str, Any]) -> None:
    """Absence means the runtime has not checkpointed yet, not that it failed."""

    staged = _checkpoint_plan(harness, revision=8)
    runner = _checkpoint_harness(harness, _Checkpoints(None))

    result = _run_to_completion(runner, staged.transition.transition_id)

    assert result.snapshot.transition.state != TRANSITION_STATE_COMPLETED
    bound = harness["checkpoint_bindings"].observe_transition_checkpoint_binding(
        tenant_id="tenant-a",
        run_id="run-a",
        effect_id=staged.effects[1].effect_id,
    )
    assert bound.receipt is None


def test_a_checkpoint_at_a_later_revision_is_never_silently_bound(
    harness: dict[str, Any],
) -> None:
    """Binding a drifted revision would attribute state the run never saw."""

    staged = _checkpoint_plan(harness, revision=8)
    runner = _checkpoint_harness(harness, _Checkpoints(11))

    result = _run_to_completion(runner, staged.transition.transition_id)

    assert result.snapshot.transition.state != TRANSITION_STATE_COMPLETED
    bound = harness["checkpoint_bindings"].observe_transition_checkpoint_binding(
        tenant_id="tenant-a",
        run_id="run-a",
        effect_id=staged.effects[1].effect_id,
    )
    assert bound.receipt is None


def test_the_same_composition_serves_the_langgraph_runtime(
    harness: dict[str, Any],
) -> None:
    """Runtime is a parameter, not a fork: LangGraph gets the same adapters.

    The registry is an exact runtime/kind pair, so a LangGraph registry must
    not resolve Native effects and vice versa — that exactness is what stops
    one runtime's effect from being executed under another's fencing.
    """

    registry = build_native_transition_effect_registry(
        ownership_authority=harness["ownership"],
        ownership_reads=harness["ownership"],
        event_authority=harness["events"],
        event_reads=harness["events"],
        queue_reservations=harness["queue_reservations"],
        runtime_id=TRANSITION_RUNTIME_LANGGRAPH,
        clock=lambda: _NOW,
    )

    assert registry.resolve(runtime_id=TRANSITION_RUNTIME_LANGGRAPH, effect_kind=EFFECT_QUEUE_RESERVE)
    with pytest.raises(Exception, match="executor_missing"):
        registry.resolve(runtime_id=TRANSITION_RUNTIME_NATIVE, effect_kind=EFFECT_QUEUE_RESERVE)


def test_a_langgraph_plan_reserves_its_ingest_slot_before_any_event(
    harness: dict[str, Any],
) -> None:
    store = _store(runtime_id=TRANSITION_RUNTIME_LANGGRAPH)
    admission = WorkflowCommandTransitionAdmissionService(
        store,
        transition_reader=store,
        intent_factory=NativeCommandTransitionIntentFactory(
            events=harness["events"],
            transitions=store,
            runtime_id=TRANSITION_RUNTIME_LANGGRAPH,
            reserves_queue_slot=True,
        ),
        clock=lambda: _NOW,
    )

    staged = admission.stage_or_adopt(
        receipt=_receipt(),
        binding=_binding(runtime_id=TRANSITION_RUNTIME_LANGGRAPH),
    )

    kinds = [effect.kind for effect in staged.effects]
    assert staged.transition.runtime_id == TRANSITION_RUNTIME_LANGGRAPH
    assert kinds.index(EFFECT_QUEUE_RESERVE) < kinds.index(EFFECT_EVENT_APPEND)


def _signing_key_ring() -> Ed25519SigningKeyRing:
    return Ed25519SigningKeyRing(
        {"grant-key-v1": base64.b64encode(bytes([1]) * 32)},
        active_key_id="grant-key-v1",
    )


def _planned_grant() -> PlannedAuthorizationGrant:
    node = ExecutionNode(
        node_id="step-a",
        allowed_tools=("shell",),
        input_artifacts=("in.md",),
        budget=ExecutionBudget(max_attempts=2, timeout_seconds=30.0),
    )
    plan = ExecutionPlan(
        tenant_id="tenant-a",
        plan_id="plan-a",
        workflow_id="workflow-a",
        policy_version="policy-v1",
        nodes=(node,),
    )
    return PlannedAuthorizationGrant(
        grant=ExecutionPlanGrantPolicy().derive(plan, step_id="step-a"),
        signing_key_ring=_signing_key_ring(),
    )


def test_a_langgraph_ingest_is_preceded_by_a_hub_owned_grant_intent(
    harness: dict[str, Any],
) -> None:
    """CAC-014's ordering: nothing reaches a worker before the Hub authorized it.

    The grant needs only plan-time inputs, so it can be planned ahead of the
    ingest without the runner having to feed one effect's result into another.
    """

    store = _store(runtime_id=TRANSITION_RUNTIME_LANGGRAPH)
    admission = WorkflowCommandTransitionAdmissionService(
        store,
        transition_reader=store,
        intent_factory=NativeCommandTransitionIntentFactory(
            events=harness["events"],
            transitions=store,
            runtime_id=TRANSITION_RUNTIME_LANGGRAPH,
            reserves_queue_slot=True,
            authorization=_planned_grant(),
        ),
        clock=lambda: _NOW,
    )

    staged = admission.stage_or_adopt(
        receipt=_receipt(),
        binding=_binding(runtime_id=TRANSITION_RUNTIME_LANGGRAPH),
    )

    kinds = [effect.kind for effect in staged.effects]
    assert kinds.index(EFFECT_AUTHORIZATION_GRANT) < kinds.index(EFFECT_QUEUE_RESERVE)
    assert kinds.index(EFFECT_QUEUE_RESERVE) < kinds.index(EFFECT_EVENT_APPEND)
    assert tuple(effect.ordinal for effect in staged.effects) == (1, 2, 3, 4, 5)


def test_a_grant_carrying_plan_stays_byte_deterministic(harness: dict[str, Any]) -> None:
    """A replan must produce the same signed intent, or recovery breaks."""

    def _plan() -> Any:
        store = _store()
        factory = NativeCommandTransitionIntentFactory(
            events=harness["events"],
            transitions=store,
            authorization=_planned_grant(),
        )
        return factory.build(
            receipt=_receipt(),
            binding=_binding(),
            transition_id="wft-deterministic",
            planned_at=_NOW,
        )

    assert _plan().transition.effect_fingerprint == _plan().transition.effect_fingerprint


def test_a_grant_without_its_signing_key_is_refused() -> None:
    with pytest.raises(WorkflowTransitionNativeCompositionError, match="signing_key_ring_invalid"):
        PlannedAuthorizationGrant(grant=_planned_grant().grant, signing_key_ring=object())


def test_registry_resolves_exactly_the_effect_kinds_that_have_an_adapter(
    harness: dict[str, Any],
) -> None:
    registry = build_native_transition_effect_registry(
        ownership_authority=harness["ownership"],
        ownership_reads=harness["ownership"],
        event_authority=harness["events"],
        event_reads=harness["events"],
        queue_reservations=harness["queue_reservations"],
        clock=lambda: _NOW,
    )

    for kind in (EFFECT_OWNERSHIP_RESERVE, EFFECT_QUEUE_RESERVE, EFFECT_EVENT_APPEND):
        assert registry.resolve(runtime_id=TRANSITION_RUNTIME_NATIVE, effect_kind=kind)
    # checkpoint_save has no adapter, so it must not resolve: an effect kind
    # that resolves is an effect kind that can run.
    with pytest.raises(Exception, match="executor_missing"):
        registry.resolve(runtime_id=TRANSITION_RUNTIME_NATIVE, effect_kind="checkpoint_save")


def test_the_grant_effect_only_resolves_when_both_verifiers_are_configured(
    harness: dict[str, Any],
) -> None:
    registry = build_native_transition_effect_registry(
        ownership_authority=harness["ownership"],
        ownership_reads=harness["ownership"],
        event_authority=harness["events"],
        event_reads=harness["events"],
        queue_reservations=harness["queue_reservations"],
        clock=lambda: _NOW,
    )

    with pytest.raises(Exception, match="executor_missing"):
        registry.resolve(runtime_id=TRANSITION_RUNTIME_NATIVE, effect_kind=EFFECT_AUTHORIZATION_GRANT)


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
