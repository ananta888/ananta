from __future__ import annotations

from agent.services.langgraph_workflow_control_bridge import (
    LangGraphWorkflowControlBridge,
)
from agent.services.workflow_adapter_task_queue_service import (
    WorkflowAdapterTaskReceipt,
)
from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_control_bindings import (
    InMemoryWorkflowControlBindingStore,
    WorkflowControlRunBinding,
)
from agent.services.workflow_control_service import RuntimeSelection, WorkflowPrincipal
from agent.services.workflow_provider_selection_service import (
    HubConfiguredWorkflowProviderDecisionService,
    WorkflowProviderDecision,
)
from agent.services.workflow_runtime import (
    ExecutionNode,
    ExecutionPlan,
    HmacKeyRing,
    InMemoryReplayNonceStore,
)
from agent.services.workflow_runtime.commands import WorkflowCommandVerifier
from ananta_contracts.langgraph_hub_node import (
    LANGGRAPH_EXECUTION_CAPABILITIES,
    langgraph_node_result,
)


class _Queue:
    def __init__(self) -> None:
        self.submissions = []
        self.tasks: dict[str, dict] = {}
        self.status_calls = 0
        self.inspect_calls = 0

    def submit(self, submission):
        self.submissions.append(submission)
        task_id = f"task-{submission.step_id}"
        self.tasks[task_id] = {
            "hub_task_id": task_id,
            "status": "created",
        }
        return WorkflowAdapterTaskReceipt(
            hub_task_id=task_id,
            workflow_id=submission.workflow_id,
            run_id=submission.run_id,
            step_id=submission.step_id,
            operation_id=f"operation-{submission.step_id}",
            adapter_kind="langgraph",
            command=submission.command,
            accepted=True,
            status="created",
        )

    def status(self, **scope):
        self.status_calls += 1
        return dict(self.tasks[scope["hub_task_id"]])

    def inspect(self, **scope):
        self.inspect_calls += 1
        return dict(self.tasks[scope["hub_task_id"]])

    def cancel(self, **scope):
        task = self.tasks[scope["hub_task_id"]]
        task["status"] = "cancelled"
        return dict(task)

    def history(self, **scope):
        del scope
        return ()

    def complete(self, step_id: str, *, plan_hash: str) -> None:
        task_id = f"task-{step_id}"
        self.tasks[task_id] = {
            "hub_task_id": task_id,
            "status": "completed",
            "result": {
                "adapter_result": {
                    "artifacts": [
                        langgraph_node_result(
                            node_id=step_id,
                            status="completed",
                            plan_hash=plan_hash,
                            value={"node": step_id},
                        )
                    ]
                }
            },
        }


class _ProviderDecisions:
    def decide(self, requirement):
        assert requirement.requires_provider is False
        return WorkflowProviderDecision(
            status="not_required",
            reason_code="provider_transport_not_required",
        )


def _subject(
    *,
    command: str = "dry_run",
    node_metadata: dict | None = None,
    provider_decisions=None,
):
    plan = ExecutionPlan(
        tenant_id="tenant-a",
        plan_id="plan-a",
        workflow_id="workflow-a",
        policy_version="policy-v1",
        nodes=(
            ExecutionNode(
                node_id="branch-a",
                metadata=dict(node_metadata or {}),
            ),
            ExecutionNode(node_id="branch-b"),
        ),
        capabilities=tuple(sorted(LANGGRAPH_EXECUTION_CAPABILITIES)),
        metadata={"parallel_limit": 2},
    )
    request = WorkflowRequest(
        workflow_id=plan.workflow_id,
        metadata={
            "adapter_command": command,
            "tenant_parallel_limit": 2,
            "worker_parallel_limit": 2,
        },
    )
    bindings = InMemoryWorkflowControlBindingStore()
    bindings.put(
        WorkflowControlRunBinding(
            tenant_id=plan.tenant_id,
            subject_id="owner-a",
            workflow_id=plan.workflow_id,
            run_id="run-a",
            runtime_id="langgraph",
            plan_hash=plan.plan_hash,
            policy_version=plan.policy_version,
            checkpoint_id="checkpoint-a",
            request=request,
            execution_plan=plan.to_dict(),
        )
    )
    queue = _Queue()
    key_ring = HmacKeyRing({"key-a": "x" * 32}, active_key_id="key-a")
    bridge = LangGraphWorkflowControlBridge(
        queue=queue,
        bindings=bindings,
        command_verifier=WorkflowCommandVerifier(
            key_ring,
            InMemoryReplayNonceStore(),
        ),
        provider_decisions=provider_decisions or _ProviderDecisions(),
        reconciler_id="reconciler-a",
    )
    return bridge, bindings, queue, plan


def test_hub_fans_out_one_task_per_node_and_recovers_without_duplicates() -> None:
    bridge, bindings, queue, plan = _subject()
    principal = WorkflowPrincipal("tenant-a", "owner-a")
    selection = RuntimeSelection(
        runtime_id="langgraph",
        capabilities=LANGGRAPH_EXECUTION_CAPABILITIES,
        mode="live",
        reason_code="runtime_selected",
    )

    handle = bridge.start(
        principal=principal,
        plan=plan,
        run_id="run-a",
        selection=selection,
        authorization_envelope={
            "schema": "ananta.workflow_route_control.v1",
            "tenant_id": "tenant-a",
            "subject_id": "owner-a",
            "workflow_id": "workflow-a",
            "run_id": "run-a",
        },
    )

    assert handle.status == "running"
    status = bindings.last_status(plan.workflow_id)
    assert status is not None
    assert all("gate" not in step for step in status["steps"])
    assert [value.step_id for value in queue.submissions] == ["branch-a", "branch-b"]
    assert all(value.payload["execution_scope"] == "single_hub_node" for value in queue.submissions)

    # Query is a persisted read; it must not acknowledge Worker task state.
    assert bridge.query(principal=principal, run_id="run-a")["status"] == "running"
    assert queue.status_calls == 0
    assert queue.inspect_calls == 0

    queue.complete("branch-a", plan_hash=plan.plan_hash)
    queue.complete("branch-b", plan_hash=plan.plan_hash)
    restarted = LangGraphWorkflowControlBridge(
        queue=queue,
        bindings=bindings,
        command_verifier=WorkflowCommandVerifier(
            HmacKeyRing({"key-a": "x" * 32}, active_key_id="key-a"),
            InMemoryReplayNonceStore(),
        ),
        provider_decisions=_ProviderDecisions(),
        reconciler_id="reconciler-b",
    )

    assert restarted.reconcile_active() == {
        "runtime_id": "langgraph",
        "processed": 1,
        "failed": [],
    }
    assert restarted.query(principal=principal, run_id="run-a")["status"] == "completed"
    assert len(queue.submissions) == 2
    assert restarted.reconcile_active()["processed"] == 0


def test_bridge_binds_compiled_step_routing_before_worker_delegation() -> None:
    decisions = HubConfiguredWorkflowProviderDecisionService(
        lambda: {
            "model_profiles_path": ("config/models/local-ollama-phi-gemma-rtx3080.model_profiles.yaml"),
            "model_routing_path": ("config/models/local-ollama-phi-gemma-rtx3080.model_routing.json"),
        }
    )
    bridge, _bindings, queue, plan = _subject(
        command="execute",
        node_metadata={
            "model_routing": {
                "model_role": "reasoning",
                "preferred_profile_id": ("local_ollama_gemma4_e4b_reasoning"),
                "fallback_group_id": "local_phi_to_gemma_reasoning",
            }
        },
        provider_decisions=decisions,
    )
    principal = WorkflowPrincipal("tenant-a", "owner-a")
    selection = RuntimeSelection(
        runtime_id="langgraph",
        capabilities=LANGGRAPH_EXECUTION_CAPABILITIES,
        mode="live",
        reason_code="runtime_selected",
    )

    bridge.start(
        principal=principal,
        plan=plan,
        run_id="run-a",
        selection=selection,
        authorization_envelope={
            "schema": "ananta.workflow_route_control.v1",
            "tenant_id": "tenant-a",
            "subject_id": "owner-a",
            "workflow_id": "workflow-a",
            "run_id": "run-a",
        },
    )

    first = queue.submissions[0]
    assert first.primary_profile_id == ("local_ollama_gemma4_e4b_reasoning")
    assert first.provider_binding.model_id == ("ananta-gemma4-reasoning-8k")
    assert first.model_routing["fallback_group_id"] == ("local_phi_to_gemma_reasoning")
    assert [item.profile_id for item in first.provider_profile_bindings] == [
        "local_ollama_gemma4_e4b_reasoning",
        "local_ollama_phi4_mini",
    ]
