from __future__ import annotations

from dataclasses import replace

from agent.services.dspy_native_program_service import DspyNativeProgramRenderer, DspyNativeProgramRuntime
from tests.dspy_optimization.helpers import program
from worker.optimization.dspy.metric_bridge import DspyDeterministicMetricBridge


class FixedExecutor:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    def complete(self, **_kwargs) -> str:
        self.calls += 1
        return self.output


def test_planning_native_runtime_uses_promoted_program_and_falls_back_safely() -> None:
    candidate = replace(
        program(),
        scope={
            "language": "en",
            "planning_mode": "structured",
            "model_profile": "local-default",
            "output_schema": "planning-tasks-v1",
        },
    )
    executor = FixedExecutor('{"tasks":[{"id":"T1","title":"Build","description":"x","depends_on":[]}]}')
    runtime = DspyNativeProgramRuntime(executor)
    result = runtime.execute(
        program=candidate,
        inputs={"goal": "Build it", "constraints": []},
        baseline=lambda: {"tasks": []},
    )
    assert result["variant"] == "dspy_promoted"
    assert result["fallback_used"] is False
    broken = DspyNativeProgramRuntime(FixedExecutor("not-json")).execute(
        program=candidate,
        inputs={"goal": "Build it", "constraints": []},
        baseline=lambda: {"tasks": [{"id": "baseline"}]},
    )
    assert broken["variant"] == "baseline"
    assert broken["reason_code"] == "dspy_native_program_failed"


def test_native_repair_is_bounded_and_never_invents_fields() -> None:
    renderer = DspyNativeProgramRenderer()
    repaired = renderer.parse(program(), '```json\n{"tasks": []}\n```')
    assert repaired["parse_state"] == "repaired"
    assert repaired["transformations"] == ["strip_json_fence"]


def test_task_specific_metrics_fail_closed_on_policy_and_source_errors() -> None:
    bridge = DspyDeterministicMetricBridge()
    planning = bridge.evaluate(
        program_kind="planning_structured_tasks",
        expected={"tasks": [{"id": "T1"}]},
        actual={"tasks": [{"id": "T1", "title": "A", "description": "B", "depends_on": []}]},
    )
    assert planning["passed"] is True
    rag = bridge.evaluate(
        program_kind="rag_answer",
        expected={"answer": "x", "citations": ["SRC_a"]},
        actual={"answer": "x", "citations": ["SRC_other"]},
        allowed_source_refs=["SRC_a"],
    )
    assert rag["passed"] is False
    extraction = bridge.evaluate(
        program_kind="structured_extraction",
        expected={"result": {"name": "Ada", "year": 1815}},
        actual={"result": {"name": "Ada", "year": 1815}},
    )
    assert extraction["score"] == 1
