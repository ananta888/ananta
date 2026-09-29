from __future__ import annotations

from types import SimpleNamespace

from agent.services.workflow_runtime import (
    ExecutionEdge,
    ExecutionNode,
    ExecutionPlan,
    HmacKeyRing,
    RuntimeAuthorizationEnvelope,
)
from agent.services.workflow_runtime.ports import DelegatedExecutionRequest
from ananta_contracts.langgraph_hub_node import (
    LANGGRAPH_EXECUTION_CAPABILITIES,
    LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA,
    langgraph_node_result,
)
from worker.runtime.langgraph.execution_adapter import (
    LangGraphExecutionRuntimeAdapter,
)


class _SingleNodeAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **values):
        self.calls.append(values)
        payload = values["payload"]
        return SimpleNamespace(
            artifacts=[
                langgraph_node_result(
                    node_id=payload["delegated_node_id"],
                    status="completed",
                    plan_hash=payload["plan_hash"],
                    artifacts={"report": "artifact://report-a"},
                    tokens=7,
                    cost_micros=11,
                )
            ]
        )


def _plan(*, node_type: str = "task") -> ExecutionPlan:
    nodes = (ExecutionNode(node_id="step-a", node_type=node_type),)
    edges = ()
    if node_type == "merge":
        nodes = (
            ExecutionNode(node_id="source-a"),
            ExecutionNode(
                node_id="step-a",
                node_type="merge",
                metadata={"merge_strategy": "ordered-by-node-id"},
            ),
        )
        edges = (ExecutionEdge(source="source-a", target="step-a"),)
    return ExecutionPlan(
        tenant_id="tenant-a",
        plan_id="plan-a",
        workflow_id="workflow-a",
        policy_version="policy-v1",
        nodes=nodes,
        edges=edges,
        capabilities=tuple(sorted(LANGGRAPH_EXECUTION_CAPABILITIES)),
    )


def _request(plan: ExecutionPlan) -> DelegatedExecutionRequest:
    authorization = RuntimeAuthorizationEnvelope.issue(
        key_ring=HmacKeyRing({"key-a": "x" * 32}, active_key_id="key-a"),
        tenant_id=plan.tenant_id,
        workflow_id=plan.workflow_id,
        run_id="run-a",
        step_id="step-a",
        plan_hash=plan.plan_hash,
        policy_version=plan.policy_version,
        ttl_seconds=60,
        now=100,
    )
    return DelegatedExecutionRequest(
        tenant_id=plan.tenant_id,
        workflow_id=plan.workflow_id,
        run_id="run-a",
        step_id="step-a",
        attempt_id="attempt-a",
        fencing_token=3,
        plan_hash=plan.plan_hash,
        policy_version=plan.policy_version,
        authorization_envelope=authorization.to_dict(),
        parameters={
            "hub_task_id": "hub-task-a",
            "task_type": "agent_workflow",
            "execution_plan": plan.to_dict(),
            "payload": {"tenant_id": "forged-tenant"},
        },
    )


def test_execution_runtime_port_validates_and_executes_exactly_one_hub_node() -> None:
    plan = _plan()
    single_node = _SingleNodeAdapter()
    runtime = LangGraphExecutionRuntimeAdapter(single_node)

    report = runtime.validate(plan)
    result = runtime.execute(_request(plan))

    assert report.valid is True
    assert runtime.runtime_id == "langgraph"
    assert runtime.capabilities == LANGGRAPH_EXECUTION_CAPABILITIES
    assert result.status == "completed"
    assert result.artifact_refs == ("artifact://report-a",)
    assert len(single_node.calls) == 1
    payload = single_node.calls[0]["payload"]
    assert payload["schema"] == LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA
    assert payload["execution_scope"] == "single_hub_node"
    assert payload["tenant_id"] == "tenant-a"


def test_execution_runtime_port_rejects_hub_owned_merge_execution() -> None:
    plan = _plan(node_type="merge")
    single_node = _SingleNodeAdapter()
    runtime = LangGraphExecutionRuntimeAdapter(single_node)

    result = runtime.execute(_request(plan))

    assert result.status == "failed"
    assert result.reason_code == "langgraph_hub_owned_node_type"
    assert single_node.calls == []
