from __future__ import annotations

import threading
import time
from importlib.metadata import version

import pytest

from agent.providers.lc_lg import LangGraphProviderConfig
from agent.services.workflow_runtime.reference_workflows import load_reference_workflows
from agent.services.workflow_runtime.release_gate import load_workflow_release_gate_config
from tests.workflow_runtime.release_gate.evidence_helpers import emit_reference_run_evidence
from worker.adapters.langgraph_adapter import LangGraphAdapter
from worker.adapters.langgraph_execution_plan import (
    ExecutionPlanNodeOutcome,
    ExecutionPlanNodeRequest,
)
from worker.adapters.workflow_budget import WorkflowBudgetGuard


class _ReleaseExecutor:
    """The barrier fails if the release path ever becomes serial again."""

    def __init__(self, *, parallel: bool) -> None:
        self._barrier = threading.Barrier(2) if parallel else None
        self.side_effect_operations: set[str] = set()

    def execute(
        self,
        request: ExecutionPlanNodeRequest,
        *,
        budget: WorkflowBudgetGuard,
    ) -> ExecutionPlanNodeOutcome:
        del budget
        if self._barrier is not None and request.node.node_id.startswith("branch-"):
            self._barrier.wait(timeout=2)
            time.sleep(0.02 if request.node.node_id == "branch-a" else 0.001)
        if request.node.side_effect_class != "none":
            operation = str(request.node.metadata.get("operation_name") or "")
            if operation:
                self.side_effect_operations.add(operation)
        return ExecutionPlanNodeOutcome.completed(
            {"node": request.node.node_id, "branch": request.node.node_id.removeprefix("branch-")}
        )


def _adapter(*, parallel: bool = False) -> tuple[LangGraphAdapter, _ReleaseExecutor]:
    executor = _ReleaseExecutor(parallel=parallel)
    return LangGraphAdapter(
        LangGraphProviderConfig(
            enabled=True,
            mode="local_live",
            checkpoint_policy="local_ephemeral",
            state_policy="ephemeral",
            max_iterations=10,
            max_nodes=10,
            external_calls_allowed=False,
            human_in_loop_required_for=[],
        ),
        execution_plan_node_executor=executor,
    ), executor


def _payload(scenario_id: str) -> dict:
    scenario = next(item for item in load_reference_workflows() if item.scenario_id == scenario_id)
    return {
        "execution_plan": scenario.plan.to_dict(),
        "parallel_limits": {"plan": 2, "tenant": 2, "worker": 2},
    }


@pytest.mark.parametrize("iteration", range(1, 11))
@pytest.mark.parametrize(
    "scenario_id",
    ("research", "code-analysis", "approval", "bounded-parallel-merge"),
)
def test_langgraph_reference_runtime_probe_is_offline_and_repeatable(
    scenario_id: str,
    iteration: int,
) -> None:
    # The production release probe must exercise the real, pinned framework.
    # A missing optional dependency is a failed/skipped release command, never
    # evidence produced by the manual compatibility walker.
    pytest.importorskip("langgraph.graph", reason="pinned LangGraph release runtime is not installed")
    assert version("langgraph") == "0.2.76"
    scenario = next(item for item in load_reference_workflows() if item.scenario_id == scenario_id)
    adapter, executor = _adapter(parallel=scenario_id == "bounded-parallel-merge")
    payload = _payload(scenario_id)
    dry_run = adapter.dry_run(
        task_id=f"release-{scenario_id}-{iteration}",
        task_type="agent_workflow",
        payload=payload,
    )

    assert dry_run.blocked is False
    if scenario_id == "approval":
        assert dry_run.approval_required is True
        waiting = adapter.execute(
            task_id=f"release-{scenario_id}-{iteration}",
            task_type="agent_workflow",
            payload=payload,
        )
        assert waiting.status == "blocked"
        assert waiting.reason_code == "human_approval_required"
        payload["approved_gates"] = ["publish-approval"]
        completed = adapter.execute(
            task_id=f"release-{scenario_id}-{iteration}",
            task_type="agent_workflow",
            payload=payload,
        )
        assert completed.status == "success"
    else:
        completed = adapter.execute(
            task_id=f"release-{scenario_id}-{iteration}",
            task_type="agent_workflow",
            payload=payload,
        )
        assert completed.status == "success"
        assert completed.artifacts
        if scenario_id == "bounded-parallel-merge":
            artifact = completed.artifacts[0]
            assert artifact["max_observed_parallelism"] == 2
            assert artifact["batches"] == [["branch-a", "branch-b"], ["merge"]]
            merge = next(item for item in artifact["records"] if item["node_id"] == "merge")
            assert merge["value"] == [
                {"branch": "a", "node": "branch-a"},
                {"branch": "b", "node": "branch-b"},
            ]

    artifact = completed.artifacts[0]
    observed_events = {
        str(event.get("event") or "")
        for event in artifact["trace"]
        if str(event.get("event") or "")
    }
    if executor.side_effect_operations:
        observed_events.add("workflow.side_effect.completed")
    emit_reference_run_evidence(
        runtime_id="langgraph",
        scenario=scenario,
        iteration=iteration,
        run_id=f"langgraph-reference-{scenario_id}-{iteration}",
        terminal_status="completed",
        event_types=observed_events,
        artifact_ids=artifact["artifacts"],
        gate_ids=artifact.get("approved_gates") or (),
        side_effect_operations=executor.side_effect_operations,
        policy_decisions=scenario.invariants.required_policy_decisions,
    )


def test_langgraph_release_capabilities_are_only_compared_for_common_reference_workflows() -> None:
    scenarios = {item.scenario_id: item for item in load_reference_workflows()}
    requirement = load_workflow_release_gate_config().requirement_for("langgraph")

    assert requirement.required_scenarios == (
        "research",
        "code-analysis",
        "approval",
        "bounded-parallel-merge",
    )
    assert all(
        set(scenarios[scenario_id].plan.capabilities).issubset(requirement.capabilities)
        for scenario_id in requirement.required_scenarios
    )
    assert scenarios["long-running-resume"].support_for("langgraph") == "incompatible"
