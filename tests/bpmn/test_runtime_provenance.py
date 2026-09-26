"""Canonical activation lineage survives status projection without variables."""

from types import SimpleNamespace

from agent.services.bpmn_runtime_provenance import bpmn_step_provenance
from agent.services.workflow_runtime.execution_plan import WorkflowRequestExecutionPlanAdapter
from agent.services.workflow_runtime_status_projection import _project_public_steps
from tests.bpmn.test_xml_regions import compile_xml, loop_xml


def test_iteration_provenance_comes_from_plan_not_raw_runtime_claims():
    _, request = compile_xml(loop_xml())
    plan = WorkflowRequestExecutionPlanAdapter.adapt(request, tenant_id="synthetic", policy_version="test")
    lineage = bpmn_step_provenance(plan.to_dict())
    bodies = [node for node in plan.nodes if node.metadata["bpmn_activation_origin"]["role"] == "body"]
    assert {lineage[node.node_id]["iteration"] for node in bodies} == {0, 1, 2}
    assert all(lineage[node.node_id]["element_id"] == "again" for node in bodies)
    raw = {
        "steps": [
            {
                "step_id": node.node_id,
                "status": "pending",
                "iteration": 999,
                "element_id": "forged",
                "activation_id": "forged",
                "variables": {"private_note": "no"},
            }
            for node in plan.nodes
        ]
    }
    binding = SimpleNamespace(execution_plan=plan.to_dict(), request=request)
    projected = _project_public_steps(raw, binding=binding, allow_missing=False)
    for step in projected:
        assert step == {"step_id": step["step_id"], "status": "pending", **lineage[step["step_id"]]}


def test_legacy_status_gets_no_synthetic_bpmn_lineage():
    assert bpmn_step_provenance({"metadata": {}, "nodes": [{"node_id": "legacy"}]}) == {}
