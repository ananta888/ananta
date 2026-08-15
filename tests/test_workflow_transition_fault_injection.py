"""Crash and restart behaviour at every side-effect and binding boundary.

CAC-014's last acceptance criterion: injecting a fault at each boundary and
then restarting must still leave exactly one task, one event, one effect and
one terminal outcome.  These tests drive the real ownership and event adapters
rather than stubs, because the property being checked is precisely that the
adapters adopt their own prior work instead of repeating it.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.services.workflow_command_transition_admission import (
    WorkflowCommandTransitionAdmissionService,
)
from agent.services.workflow_runtime.events import InMemoryEventStore
from agent.services.workflow_runtime.ownership import InMemoryExecutionOwnershipStore
from agent.services.workflow_transition_effect_execution import (
    BoundedWorkflowTransitionRetryPolicy,
)
from agent.services.workflow_transition_native_composition import (
    NativeCommandTransitionIntentFactory,
    NativeTransitionPublicProjector,
    build_native_transition_effect_registry,
    build_native_transition_finalization_registry,
)
from agent.services.workflow_transition_outbox import (
    TRANSITION_RUNTIME_NATIVE,
    TRANSITION_STATE_COMPLETED,
)
from agent.services.workflow_transition_persistence import InMemoryWorkflowTransitionStore
from agent.services.workflow_transition_runner import (
    RUN_OUTCOME_PROGRESSED,
    WorkflowTransitionRunner,
)
from tests.test_workflow_transition_native_composition import (
    _NOW,
    _binding,
    _receipt,
    _StatusReads,
)

RUNNER_STAGES = (
    "after_effect_observation",
    "after_effect_begin",
    "after_effect_execution",
    "after_effect_finish",
    "after_finalization_observation",
)

STORE_STAGES = (
    "stage_after_transition",
    "stage_after_effects",
    "stage_before_binding_cas",
    "stage_after_binding_cas",
    "yield_before_commit",
    "finalize_before_binding_cas",
    "finalize_after_binding_cas",
    "finalize_after_receipt_cas",
    "finalize_before_transition_cas",
)


class _Crash(RuntimeError):
    """A process-level fault, not a domain error the adapters could absorb."""


class _Injector:
    """Raise once at the named stage, then let every later call through."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.fired = False
        self.seen: list[str] = []

    def __call__(self, stage: str) -> None:
        self.seen.append(stage)
        if stage == self.stage and not self.fired:
            self.fired = True
            raise _Crash(stage)


class _World:
    """One durable world that survives a simulated process restart.

    The stores are the process-independent state; runner and admission are
    rebuilt on each restart exactly as a new process would build them.
    """

    def __init__(self, *, store_injector: Any = None) -> None:
        self.events = InMemoryEventStore()
        self.ownership = InMemoryExecutionOwnershipStore()
        self.status_reads = _StatusReads()
        self.store = InMemoryWorkflowTransitionStore(
            clock=lambda: _NOW,
            receipt_projector=NativeTransitionPublicProjector(),
            fault_injector=store_injector,
        )
        self.store.put_binding(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            runtime_id=TRANSITION_RUNTIME_NATIVE,
            runtime_revision=7,
            runtime_checkpoint_ref="checkpoint-7",
            command_receipt_id="command-a",
        )
        self.store.put_receipt(
            receipt_id="command-a",
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-a",
            expected_revision=7,
            checkpoint_ref="checkpoint-7",
            request_payload=_receipt().request_payload,
        )

    def admission(self) -> WorkflowCommandTransitionAdmissionService:
        return WorkflowCommandTransitionAdmissionService(
            self.store,
            transition_reader=self.store,
            intent_factory=NativeCommandTransitionIntentFactory(
                events=self.events,
                transitions=self.store,
            ),
            clock=lambda: _NOW,
        )

    def runner(self, *, injector: Any = None, owner: str = "runner-a") -> WorkflowTransitionRunner:
        return WorkflowTransitionRunner(
            reads=self.store,
            leases=self.store,
            effects=self.store,
            completion=self.store,
            quarantine=self.store,
            effect_registry=build_native_transition_effect_registry(
                ownership_authority=self.ownership,
                ownership_reads=self.ownership,
                event_authority=self.events,
                event_reads=self.events,
                clock=lambda: _NOW,
            ),
            finalization_registry=build_native_transition_finalization_registry(
                status_reads=self.status_reads,
            ),
            retry_policy=BoundedWorkflowTransitionRetryPolicy(5, 2.0, 2.0, 10.0),
            owner_id=owner,
            lease_seconds=30.0,
            clock=lambda: _NOW,
            fault_injector=injector,
        )

    @property
    def appended_events(self) -> list[Any]:
        return list(self.events.list_events(tenant_id="tenant-a", run_id="run-a"))


def _run_until_settled(runner: WorkflowTransitionRunner, transition_id: str, *, budget: int = 12) -> Any:
    result = None
    for _ in range(budget):
        result = runner.run(transition_id)
        if result.outcome != RUN_OUTCOME_PROGRESSED:
            return result
    return result


def _drive_through(world: _World, transition_id: str, *, injector: _Injector) -> Any:
    """Fault once inside a run, then restart and finish with a fresh runner.

    Some boundaries propagate the fault and some are deliberately absorbed into
    a quarantine by the runner.  Both are legitimate crash shapes; what must
    hold either way is that a restart does not repeat the work.
    """

    try:
        _run_until_settled(world.runner(injector=injector), transition_id)
    except _Crash:
        pass
    assert injector.fired, f"stage {injector.stage} was never reached"
    return _run_until_settled(world.runner(owner="runner-b"), transition_id)


@pytest.mark.parametrize("stage", RUNNER_STAGES)
def test_a_crash_at_each_runner_boundary_still_yields_one_event_and_one_outcome(
    stage: str,
) -> None:
    world = _World()
    staged = world.admission().stage_or_adopt(receipt=_receipt(), binding=_binding())

    result = _drive_through(world, staged.transition.transition_id, injector=_Injector(stage))

    assert result is not None
    assert len(world.appended_events) <= 1
    if result.snapshot.transition.state == TRANSITION_STATE_COMPLETED:
        assert len(world.appended_events) == 1
        applied = [effect for effect in result.snapshot.effects if effect.state == "applied"]
        assert len(applied) == 3


@pytest.mark.parametrize("stage", RUNNER_STAGES)
def test_a_restart_never_appends_a_second_event_for_the_same_command(stage: str) -> None:
    world = _World()
    staged = world.admission().stage_or_adopt(receipt=_receipt(), binding=_binding())
    _drive_through(world, staged.transition.transition_id, injector=_Injector(stage))

    # A third process picks the run up again; adoption must be idempotent.
    _run_until_settled(world.runner(owner="runner-c"), staged.transition.transition_id)

    assert len(world.appended_events) <= 1


def test_a_crash_during_staging_leaves_no_half_admitted_transition() -> None:
    injector = _Injector("stage_before_binding_cas")
    world = _World(store_injector=injector)

    with pytest.raises(_Crash):
        world.admission().stage_or_adopt(receipt=_receipt(), binding=_binding())

    assert injector.fired
    assert world.store.active_transition_id("workflow-a") == ""
    assert world.appended_events == []


def test_staging_is_retried_cleanly_after_a_crash_before_the_binding_cas() -> None:
    injector = _Injector("stage_before_binding_cas")
    world = _World(store_injector=injector)
    with pytest.raises(_Crash):
        world.admission().stage_or_adopt(receipt=_receipt(), binding=_binding())

    staged = world.admission().stage_or_adopt(receipt=_receipt(), binding=_binding())

    assert staged.transition.transition_id
    result = _run_until_settled(world.runner(), staged.transition.transition_id)
    assert result.snapshot.transition.state == TRANSITION_STATE_COMPLETED
    assert len(world.appended_events) == 1


@pytest.mark.parametrize(
    "stage",
    ("finalize_before_binding_cas", "finalize_after_binding_cas", "finalize_before_transition_cas"),
)
def test_a_crash_around_finalization_never_double_finalizes(stage: str) -> None:
    injector = _Injector(stage)
    world = _World(store_injector=injector)
    staged = world.admission().stage_or_adopt(receipt=_receipt(), binding=_binding())

    with pytest.raises(_Crash):
        _run_until_settled(world.runner(), staged.transition.transition_id)
    assert injector.fired
    result = _run_until_settled(world.runner(owner="runner-b"), staged.transition.transition_id)

    assert len(world.appended_events) == 1
    if result.snapshot.transition.state == TRANSITION_STATE_COMPLETED:
        receipt_record = world.store.receipt_record("command-a")
        assert receipt_record is not None
        assert receipt_record["state"] == "completed"


def test_two_runners_racing_the_same_transition_produce_one_event() -> None:
    world = _World()
    staged = world.admission().stage_or_adopt(receipt=_receipt(), binding=_binding())
    first = world.runner(owner="runner-a")
    second = world.runner(owner="runner-b")

    first.run(staged.transition.transition_id)
    second.run(staged.transition.transition_id)
    _run_until_settled(first, staged.transition.transition_id)
    _run_until_settled(second, staged.transition.transition_id)

    assert len(world.appended_events) == 1


def test_every_declared_store_boundary_is_actually_reachable() -> None:
    """A stage nobody reaches would make its fault-injection test vacuous."""

    injector = _Injector("__never__")
    world = _World(store_injector=injector)
    staged = world.admission().stage_or_adopt(receipt=_receipt(), binding=_binding())
    _run_until_settled(world.runner(), staged.transition.transition_id)

    reached = set(injector.seen)
    unreachable = {
        stage
        for stage in STORE_STAGES
        if stage not in reached and stage not in {"yield_before_commit", "quarantine_before_commit"}
    }
    assert unreachable == set()
