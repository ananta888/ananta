from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, InMemoryEventStore
from agent.services.workflow_simulation_service import (
    DeterministicWorkflowSimulationService,
    WorkflowGoldenTraceNormalizer,
)
from tests.workflow_runtime.fakes import (
    DeterministicDeliveryBuffer,
    FakeApprovalStore,
    FakeArtifactStore,
    FakeClock,
    ScriptedFaultInjector,
    ScriptedProvider,
    ScriptedTool,
)

GOLDEN = Path(__file__).parent / "workflow_runtime" / "golden" / "simulation_trace.v1.json"


@dataclass(frozen=True)
class Result:
    status: str


class FakeRuntime:
    runtime_id = "fake-native"
    runtime_version = "1.0.0"

    def __init__(self) -> None:
        self.events = InMemoryEventStore()

    def start(self, request):
        self._append("workflow.run.started", "start", {"plan_hash": "fixture"})
        return Result("running")

    def advance(self, request):
        self._append(
            "workflow.run.completed",
            "complete",
            {"artifact_ids": ["report"], "side_effect_operations": []},
        )
        return Result("completed")

    def stream(self, request, *, after_sequence=0):
        return self.events.list_events(tenant_id="tenant-a", run_id="run-1", after_sequence=after_sequence)

    def _append(self, event_type, dedupe_key, payload):
        current = len(self.stream(None))
        event = CanonicalWorkflowEvent.build(
            tenant_id="tenant-a",
            workflow_id="workflow-a",
            run_id="run-1",
            event_type=event_type,
            correlation_id="run-1",
            causation_id="control-1",
            dedupe_key=dedupe_key,
            actor="hub",
            payload=payload,
            occurred_at=100 + current,
            event_id=f"event-{current + 1}",
        )
        self.events.append(event, expected_sequence=current)


def test_simulation_is_hard_blocked_in_production_and_never_production_eligible() -> None:
    with pytest.raises(RuntimeError, match="production_forbidden"):
        DeterministicWorkflowSimulationService(environment="production", explicitly_enabled=True)
    with pytest.raises(RuntimeError, match="not_enabled"):
        DeterministicWorkflowSimulationService(environment="test", explicitly_enabled=False)

    report = DeterministicWorkflowSimulationService(environment="test", explicitly_enabled=True).run(
        FakeRuntime(),
        {},
        fault_injector=ScriptedFaultInjector({2: ("worker_crash", True)}),
    )

    assert report.terminal_status == "completed"
    assert report.faults == ("worker_crash",)
    assert report.production_eligible is False
    assert report.runtime_id.startswith("simulation:")


def test_golden_trace_is_byte_stable_and_semantic_mutation_is_detected() -> None:
    service = DeterministicWorkflowSimulationService(environment="test", explicitly_enabled=True)
    first = service.run(FakeRuntime(), {})
    second = service.run(FakeRuntime(), {})
    normalizer = WorkflowGoldenTraceNormalizer()
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert normalizer.canonical_bytes(first.golden_trace) == normalizer.canonical_bytes(expected)
    assert normalizer.canonical_bytes(first.golden_trace) == normalizer.canonical_bytes(second.golden_trace)
    mutated = json.loads(json.dumps(first.golden_trace))
    mutated["events"][1]["payload"]["side_effect_operations"] = ["unapproved_external_write"]
    assert normalizer.canonical_bytes(mutated) != normalizer.canonical_bytes(expected)


@pytest.mark.parametrize(
    "fault_type",
    [
        "provider_timeout",
        "approval_interrupt",
        "worker_crash",
        "partial_branch_failure",
    ],
)
def test_recoverable_fault_classes_are_scriptable_and_retry_deterministically(
    fault_type: str,
) -> None:
    report = DeterministicWorkflowSimulationService(environment="test", explicitly_enabled=True).run(
        FakeRuntime(),
        {},
        fault_injector=ScriptedFaultInjector({2: (fault_type, True)}),
    )

    assert report.terminal_status == "completed"
    assert report.ticks == 3
    assert report.faults == (fault_type,)


def test_unrecoverable_partial_failure_is_scriptable_and_stops_fail_closed() -> None:
    report = DeterministicWorkflowSimulationService(environment="test", explicitly_enabled=True).run(
        FakeRuntime(),
        {},
        fault_injector=ScriptedFaultInjector({2: ("uncertain_side_effect", False)}),
    )

    assert report.terminal_status == "failed"
    assert report.faults == ("uncertain_side_effect",)


def test_all_simulation_adapters_are_deterministic_and_replaceable() -> None:
    clock = FakeClock()
    assert clock.advance(2) == 102
    provider = ScriptedProvider([{"text": "ok"}, TimeoutError("timeout")])
    tool = ScriptedTool([{"status": "ok"}])
    artifacts = FakeArtifactStore()
    approvals = FakeApprovalStore({"gate": "approved"})
    delivery = DeterministicDeliveryBuffer(["b", "a"])
    delivery.add("a", 1)
    delivery.add("b", 2)

    assert provider.invoke({"prompt": "bounded"}) == {"text": "ok"}
    with pytest.raises(TimeoutError):
        provider.invoke({"prompt": "retry"})
    assert tool.execute({"path": "safe"}) == {"status": "ok"}
    reference = artifacts.put({"answer": 42})
    assert artifacts.get(reference) == {"answer": 42}
    assert approvals.decide("gate") == "approved"
    assert delivery.drain() == (2, 1)
