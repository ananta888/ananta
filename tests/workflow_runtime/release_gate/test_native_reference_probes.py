from __future__ import annotations

from collections import Counter

import pytest

from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_runtime.reference_workflows import load_reference_workflows
from tests.test_native_graph_runtime import runtime, signed_control
from tests.workflow_runtime.release_gate.evidence_helpers import emit_reference_run_evidence

SCENARIO_IDS = (
    "research",
    "code-analysis",
    "approval",
    "bounded-parallel-merge",
)


def _request(scenario_id: str, iteration: int) -> NativeGraphRequest:
    scenarios = {scenario.scenario_id: scenario for scenario in load_reference_workflows()}
    return NativeGraphRequest(
        plan=scenarios[scenario_id].plan,
        run_id=f"native-reference-{scenario_id}-{iteration}",
        control_task_id=f"native-reference-control-{scenario_id}-{iteration}",
        tenant_parallel_limit=2,
        worker_parallel_limit=2,
    )


@pytest.mark.parametrize("iteration", range(1, 11))
@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_native_reference_scenario_crosses_hub_task_boundary_ten_times(
    scenario_id: str,
    iteration: int,
) -> None:
    scenario = next(value for value in load_reference_workflows() if value.scenario_id == scenario_id)
    orchestrator, queue, handler, keys, ledger, _stores = runtime()
    request = _request(scenario_id, iteration)

    result = orchestrator.start(request)
    submissions_after_start = tuple(command.node.node_id for command in queue.submissions)
    observed_gates: set[str] = set()
    for _ in range(len(scenario.plan.nodes) + 4):
        if result.status == "waiting_for_approval":
            gate_id = result.open_gates[0]
            observed_gates.add(gate_id)
            gated_node = next(node for node in scenario.plan.nodes if node.gate_id == gate_id)
            result = orchestrator.resume(
                request,
                command=signed_control(
                    keys=keys,
                    checkpoint=result.checkpoint,
                    command_type="approve",
                    step_id=gated_node.node_id,
                    command_id=f"native-reference-approve-{iteration}",
                ),
            )
        elif result.status == "running":
            result = orchestrator.advance(request)
        else:
            break

    delegated_nodes = tuple(command.node.node_id for command in queue.submissions)
    expected_delegated_nodes = {node.node_id for node in scenario.plan.nodes if node.node_type == "task"}
    event_types = tuple(event.event_type for event in orchestrator.stream(request))

    assert result.status == "completed"
    assert result.reason_code == ""
    assert set(scenario.invariants.required_artifacts).issubset(result.artifact_refs)
    assert set(scenario.invariants.required_event_types).issubset(event_types)
    assert set(delegated_nodes) == expected_delegated_nodes
    assert Counter(delegated_nodes) == Counter({node_id: 1 for node_id in delegated_nodes})
    assert tuple(handler.calls) == delegated_nodes
    assert all(command.control_task_id == request.control_task_id for command in queue.submissions)
    assert all(command.run_id == request.run_id for command in queue.submissions)
    assert all(command.authorization.workflow_id == scenario.plan.workflow_id for command in queue.submissions)

    if scenario_id == "bounded-parallel-merge":
        assert submissions_after_start == ("branch-a", "branch-b")
        assert result.completed_node_ids == ("branch-a", "branch-b", "merge")
        assert result.artifact_refs["merged-result"].startswith("artifact://native/")
    if scenario_id == "approval":
        assert result.open_gates == ()
        assert len(ledger._records) == 1  # noqa: SLF001 - release probe inspects the reference ledger
        assert {record.status for record in ledger._records.values()} == {  # noqa: SLF001
            "completed"
        }
        assert "workflow.approval.granted" in event_types
        assert "workflow.side_effect.completed" in event_types
    else:
        assert ledger._records == {}  # noqa: SLF001 - no reference scenario may hide a write

    budget_usage = dict(result.checkpoint.state.runtime_metadata.get("budget_usage") or {})
    attempts = dict(result.checkpoint.state.runtime_metadata.get("attempts") or {})
    budget_usage["attempts"] = max(attempts.values(), default=0)
    emit_reference_run_evidence(
        runtime_id="native",
        scenario=scenario,
        iteration=iteration,
        run_id=request.run_id,
        terminal_status=result.status,
        event_types=event_types,
        artifact_ids=result.artifact_refs,
        gate_ids=observed_gates,
        side_effect_operations={
            record.declared_operation for record in ledger._records.values()  # noqa: SLF001
        },
        policy_decisions=scenario.invariants.required_policy_decisions,
        budget_usage=budget_usage,
    )
