from __future__ import annotations

from dataclasses import replace

import pytest
from jsonschema import ValidationError
from jsonschema import validate as validate_json_schema

from agent.services.workflow_backend import WorkflowRequest, WorkflowStepRequest
from agent.services.workflow_runtime import (
    EXECUTION_PLAN_JSON_SCHEMA,
    ArtifactContract,
    ContractValidationError,
    ExecutionEdge,
    ExecutionNode,
    ExecutionPlan,
    WorkflowRequestExecutionPlanAdapter,
)


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        tenant_id="tenant-a",
        plan_id="plan-1",
        workflow_id="workflow-1",
        policy_version="policy-7",
        capabilities=("llm",),
        artifacts=(ArtifactContract("input"), ArtifactContract("output")),
        nodes=(
            ExecutionNode(
                "analyze",
                required_capabilities=("llm",),
                input_artifacts=("input",),
                output_artifacts=("output",),
            ),
            ExecutionNode("review", input_artifacts=("output",)),
        ),
        edges=(ExecutionEdge("analyze", "review", {"op": "eq", "field": "result.ok", "value": True}),),
    )


def test_execution_plan_roundtrip_hash_and_json_schema_are_stable() -> None:
    plan = _plan()
    plan.assert_valid()
    payload = plan.to_dict()

    validate_json_schema(payload, EXECUTION_PLAN_JSON_SCHEMA)
    restored = ExecutionPlan.from_mapping(payload)

    assert restored.plan_hash == plan.plan_hash == payload["plan_hash"]
    assert restored.to_dict() == payload


def test_execution_plan_rejects_payload_tampered_after_hashing() -> None:
    payload = _plan().to_dict()
    payload["nodes"][0]["task_kind"] = "unexpected"

    with pytest.raises(ContractValidationError, match="hash_mismatch"):
        ExecutionPlan.from_mapping(payload)


@pytest.mark.parametrize(
    ("mutate", "issue"),
    [
        (
            lambda plan: replace(plan, edges=(ExecutionEdge("analyze", "review"), ExecutionEdge("review", "analyze"))),
            "execution_plan_cycle",
        ),
        (
            lambda plan: replace(plan, nodes=(replace(plan.nodes[0], required_capabilities=("gpu",)), *plan.nodes[1:])),
            "capability_not_declared",
        ),
        (
            lambda plan: replace(plan, metadata={"allowed_tools": ["shell"]}),
            "reserved_metadata_key",
        ),
    ],
)
def test_execution_plan_rejects_invalid_graph_or_scope(mutate, issue: str) -> None:
    issues = mutate(_plan()).validate()
    assert issue in {value.code for value in issues}


def test_workflow_request_v1_is_read_through_compatibility_adapter() -> None:
    request = WorkflowRequest(
        workflow_id="workflow-old",
        plan_id="plan-old",
        policy_scope={"scope": "worker"},
        input_artifacts=("brief",),
        steps=(
            WorkflowStepRequest(
                step_id="build",
                allowed_tools=("git",),
                input_artifacts=("brief",),
                output_artifacts=("patch",),
            ),
            WorkflowStepRequest(step_id="approve", depends_on=("build",), gate=True),
        ),
    )

    plan = WorkflowRequestExecutionPlanAdapter.adapt(
        request,
        tenant_id="tenant-a",
        policy_version="policy-v1",
    )

    assert plan.workflow_id == "workflow-old"
    assert plan.nodes[0].allowed_tools == ("git",)
    assert plan.edges == (ExecutionEdge("build", "approve"),)
    assert plan.gates[0].gate_id == "gate:approve"
    assert plan.metadata["adapted_from"] == "ananta.workflow_request.v1"


def test_legacy_request_execution_budget_is_explicit_and_bounded() -> None:
    request = WorkflowRequest(
        workflow_id="workflow-budgeted",
        steps=(WorkflowStepRequest(step_id="build"),),
        policy_scope={"scope": "worker"},
        metadata={
            "execution_budget": {
                "max_attempts": 2,
                "timeout_seconds": 45,
            }
        },
    )

    plan = WorkflowRequestExecutionPlanAdapter.adapt(
        request,
        tenant_id="tenant-a",
        policy_version="policy-v1",
    )

    assert plan.budget.max_attempts == 2
    assert plan.budget.timeout_seconds == 45


def test_legacy_request_rejects_non_object_execution_budget() -> None:
    request = WorkflowRequest(
        workflow_id="workflow-invalid-budget",
        steps=(WorkflowStepRequest(step_id="build"),),
        policy_scope={"scope": "worker"},
        metadata={"execution_budget": "unbounded"},
    )

    with pytest.raises(ValueError, match="legacy_execution_budget_invalid"):
        WorkflowRequestExecutionPlanAdapter.adapt(
            request,
            tenant_id="tenant-a",
            policy_version="policy-v1",
        )


def test_unknown_dependency_in_legacy_request_fails_before_delegation() -> None:
    request = WorkflowRequest(
        workflow_id="workflow-old",
        plan_id="plan-old",
        steps=(WorkflowStepRequest(step_id="build", depends_on=("missing",)),),
    )
    with pytest.raises(ContractValidationError):
        WorkflowRequestExecutionPlanAdapter.adapt(
            request,
            tenant_id="tenant-a",
            policy_version="policy-v1",
        )


@pytest.mark.parametrize(
    ("metadata", "reason_code"),
    [
        (
            {"merge_strategy": "completion-order", "partial_failure": "fail"},
            "merge_strategy_unsupported",
        ),
        (
            {"merge_strategy": "ordered-by-node-id", "partial_failure": "ignore"},
            "merge_partial_failure_policy_invalid",
        ),
    ],
)
def test_merge_declaration_is_rejected_by_contract_and_json_schema(
    metadata: dict,
    reason_code: str,
) -> None:
    raw = {
        "tenant_id": "tenant-a",
        "plan_id": "invalid-merge",
        "workflow_id": "invalid-merge",
        "policy_version": "policy-v1",
        "nodes": [
            {"id": "branch"},
            {"id": "merge", "node_type": "merge", "metadata": metadata},
        ],
        "edges": [{"from": "branch", "to": "merge"}],
    }
    with pytest.raises(ContractValidationError, match=reason_code):
        ExecutionPlan.from_mapping(raw)

    serialized = ExecutionPlan.from_mapping(
        {
            **raw,
            "nodes": [
                {"id": "branch"},
                {
                    "id": "merge",
                    "node_type": "merge",
                    "metadata": {
                        "merge_strategy": "ordered-by-node-id",
                        "partial_failure": "fail",
                    },
                },
            ],
        }
    ).to_dict()
    serialized["nodes"][1]["metadata"] = metadata
    with pytest.raises(ValidationError):
        validate_json_schema(serialized, EXECUTION_PLAN_JSON_SCHEMA)
