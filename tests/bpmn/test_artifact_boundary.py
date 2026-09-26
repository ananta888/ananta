"""Missing Hub artifact admission cannot become a completed BPMN workflow."""

from dataclasses import replace

import pytest

from agent.services.workflow_runtime import ArtifactContract
from agent.services.workflow_runtime.errors import ContractValidationError
from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml


def test_bpmn_artifact_contract_denies_even_a_declared_worker_reference():
    plan = execution_plan(compile_request(xor_xml()))
    node = next(node for node in plan.nodes if node.node_id == "yes_task")
    plan = replace(
        plan,
        nodes=tuple(replace(item, output_artifacts=("report",)) if item == node else item for item in plan.nodes),
        artifacts=(ArtifactContract(artifact_id="report"),),
    )
    with pytest.raises(ContractValidationError, match="bpmn_artifact_ingress_unavailable"):
        plan.assert_valid()
    # The new fail-closed boundary is not a breaking legacy artifact migration.
    legacy = replace(
        plan, metadata={key: value for key, value in plan.metadata.items() if key != "bpmn_definition_hash"}
    )
    assert "bpmn_artifact_ingress_unavailable" not in {issue.code for issue in legacy.validate()}
