"""Adapter boundary between delegated ExecutionPlans and the LangGraph runtime."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.providers.lc_lg import LangGraphProviderConfig
from agent.services.workflow_runtime.components import WorkflowComponentCompiler
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from ananta_contracts.langgraph_hub_node import (
    LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA,
    langgraph_node_result,
)
from worker.adapters.langgraph_execution_plan import (
    ExecutionPlanNodeExecutor,
    ExecutionPlanNodeOutcome,
    ExecutionPlanNodeRequest,
    LangGraphExecutionPlanRuntime,
    LangGraphParallelLimits,
)
from worker.adapters.langgraph_legacy_nodes import LangGraphState
from worker.adapters.workflow_adapter_base import DryRunResult, WorkerError, WorkflowArtifactResult
from worker.adapters.workflow_audit import WorkflowAuditLog
from worker.adapters.workflow_budget import WorkflowBudgetGuard
from worker.adapters.workflow_policy_gate import WorkflowPolicyGate


@dataclass(frozen=True)
class LangGraphPlanNodeOperations:
    invoke_llm: Callable[..., None]
    invoke_tool: Callable[..., None]
    invoke_retriever: Callable[..., None]
    invoke_artifact_writer: Callable[..., None]
    requires_hub_checkpointing: Callable[[], bool]


class LangGraphExecutionPlanAdapter:
    """Validates a Hub-delegated plan and maps its result to worker artifacts."""

    def __init__(
        self,
        *,
        config: LangGraphProviderConfig,
        audit: WorkflowAuditLog,
        policy: WorkflowPolicyGate,
        node_operations: LangGraphPlanNodeOperations,
        checkpointer_provider: Callable[..., Any],
        checkpoint_thread_id: Callable[[str, dict[str, Any]], str],
        node_executor: ExecutionPlanNodeExecutor | None = None,
        component_compiler: WorkflowComponentCompiler | None = None,
        cancel_probe: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self._config = config
        self._audit = audit
        self._policy = policy
        self._node_operations = node_operations
        self._checkpointer_provider = checkpointer_provider
        self._checkpoint_thread_id = checkpoint_thread_id
        self._node_executor = node_executor
        self._component_compiler = component_compiler
        self._cancel_probe = cancel_probe

    def dry_run(
        self,
        *,
        task_id: str,
        task_type: str,
        payload: dict[str, Any],
        result: DryRunResult,
    ) -> DryRunResult:
        try:
            plan = self._load_plan(payload)
            limits = self._parallel_limits(payload)
            approved_gates = self._approved_gates(plan, payload)
        except (ValueError, WorkerError) as exc:
            reason_code = exc.reason_code if isinstance(exc, WorkerError) else str(exc).split(":", 1)[0]
            result.blocked = True
            result.block_reason = reason_code or "execution_plan_invalid"
            self._audit.log("dry_run_blocked", task_id=task_id, reason=result.block_reason)
            result.metadata["dry_run_audit_trace"] = self._audit.snapshot()
            return result

        if len(plan.nodes) > self._config.max_nodes:
            result.blocked = True
            result.block_reason = f"graph_too_many_nodes:{len(plan.nodes)}/{self._config.max_nodes}"
        elif not self._config.graph_allowed(plan.workflow_id):
            result.blocked = True
            result.block_reason = f"graph_not_in_allowlist:{plan.workflow_id}"
        elif bool(payload.get("cancel_requested", False)):
            result.blocked = True
            result.block_reason = "cancel_requested"

        delegated_node_id = str(payload.get("delegated_node_id") or "").strip()
        if delegated_node_id:
            try:
                delegated = _require_delegated_node(plan, delegated_node_id)
            except WorkerError as exc:
                result.blocked = True
                result.block_reason = exc.reason_code
                return result
            visible_nodes = (delegated,)
        else:
            visible_nodes = plan.nodes
        open_gates = sorted(
            {
                node.gate_id
                for node in visible_nodes
                if node.gate_id and node.gate_id not in approved_gates
            }
        )
        result.approval_required = bool(open_gates)
        result.approval_reasons = [f"execution_plan_gate:{gate_id}" for gate_id in open_gates]
        result.required_tools = sorted(
            {tool for node in visible_nodes for tool in node.allowed_tools}
        )
        if "codecompass.query" in result.required_tools:
            result.required_context_sources.append("codecompass")
        result.plan_steps = [
            {
                "step": index + 1,
                "node_id": node.node_id,
                "node_type": node.node_type,
                "task_kind": node.task_kind,
                "gate_id": node.gate_id,
                "parallel_group": node.metadata.get("parallel_group", "default"),
            }
            for index, node in enumerate(visible_nodes)
        ]
        result.estimated_tokens = sum(
            int(node.metadata.get("estimated_tokens") or 0) for node in plan.nodes
        )
        result.metadata.update(
            {
                "execution_plan_schema": plan.schema,
                "plan_hash": plan.plan_hash,
                "parallel_limits": limits.to_dict(),
                "compiled_components": dict(plan.metadata.get("compiled_components") or {}),
            }
        )
        self._audit.log(
            "dry_run_complete",
            task_id=task_id,
            task_type=task_type,
            blocked=result.blocked,
            approval_required=result.approval_required,
            plan_hash=plan.plan_hash,
        )
        result.metadata["dry_run_audit_trace"] = self._audit.snapshot()
        return result

    def run_delegated_node(
        self,
        *,
        task_id: str,
        task_type: str,
        payload: dict[str, Any],
        budget: WorkflowBudgetGuard,
    ) -> WorkflowArtifactResult:
        """Execute one Hub-selected node; never schedule another plan node."""

        if str(payload.get("schema") or "") != LANGGRAPH_HUB_NODE_PAYLOAD_SCHEMA:
            raise WorkerError(
                "langgraph_hub_node_schema_unsupported",
                "Delegated node payload must use the Hub node contract.",
            )
        plan = self._load_plan(payload)
        node_id = str(payload.get("delegated_node_id") or "").strip()
        node = _require_delegated_node(plan, node_id)
        if node.node_type in {"merge", "component"}:
            raise WorkerError(
                "langgraph_hub_node_type_forbidden",
                "Merge and component orchestration remain Hub-owned.",
            )
        approved = self._approved_gates(plan, payload)
        if node.gate_id and node.gate_id not in approved:
            raise WorkerError(
                "approval_required",
                "The Hub has not approved this delegated node.",
            )
        dependencies = payload.get("dependency_results") or {}
        if not isinstance(dependencies, dict):
            raise WorkerError(
                "langgraph_dependency_results_invalid",
                "dependency_results must be a Hub-bound object.",
            )
        incoming = {edge.source for edge in plan.edges if edge.target == node.node_id}
        if set(dependencies) - incoming:
            raise WorkerError(
                "langgraph_dependency_result_binding_mismatch",
                "A dependency result is not an incoming plan edge.",
            )
        executor = self._node_executor or _LegacyExecutionPlanNodeExecutor(self)
        def cancelled() -> bool:
            return bool(payload.get("cancel_requested", False)) or bool(
                self._cancel_probe and self._cancel_probe(task_id, dict(payload))
            )
        if cancelled():
            outcome = ExecutionPlanNodeOutcome.cancelled()
            return self._delegated_result(
                task_id=task_id,
                task_type=task_type,
                plan=plan,
                node=node,
                outcome=outcome,
            )
        request = ExecutionPlanNodeRequest(
            plan=plan,
            node=node,
            workflow_input=dict(payload.get("workflow_input") or {}),
            dependency_results=dict(dependencies),
            execution_payload={**payload, "task_id": task_id},
        )
        try:
            # A one-node StateGraph keeps framework execution real while the
            # Hub remains the only DAG scheduler/fan-out owner.
            from langgraph.graph import END, StateGraph  # type: ignore[import]

            captured: list[ExecutionPlanNodeOutcome] = []

            def execute_node(state: dict[str, Any]) -> dict[str, Any]:
                if cancelled():
                    captured.append(ExecutionPlanNodeOutcome.cancelled())
                    return state
                budget.record_step(f"hub_node:{node.node_id}")
                result = executor.execute(request, budget=budget)
                captured.append(
                    ExecutionPlanNodeOutcome.cancelled()
                    if cancelled()
                    else result
                )
                return state

            graph: Any = StateGraph(dict)
            graph.add_node(node.node_id, execute_node)
            graph.set_entry_point(node.node_id)
            graph.add_edge(node.node_id, END)
            checkpointer = self._checkpointer_provider(task_id=task_id, payload=payload)
            graph.compile(checkpointer=checkpointer).invoke(
                {},
                config={
                    "recursion_limit": self._config.max_iterations,
                    "configurable": {
                        "thread_id": self._checkpoint_thread_id(task_id, payload),
                        "checkpoint_ns": node.node_id,
                    },
                },
            )
            if len(captured) != 1:
                raise WorkerError(
                    "langgraph_hub_node_execution_count_invalid",
                    "A delegated task must execute exactly one plan node.",
                )
            outcome = captured[0]
            outcome.assert_valid()
        except WorkerError:
            raise
        except Exception as exc:
            raise WorkerError(
                "langgraph_hub_node_execution_failed",
                "The delegated LangGraph node failed.",
            ) from exc
        return self._delegated_result(
            task_id=task_id,
            task_type=task_type,
            plan=plan,
            node=node,
            outcome=outcome,
        )

    def _delegated_result(
        self,
        *,
        task_id: str,
        task_type: str,
        plan: ExecutionPlan,
        node: ExecutionNode,
        outcome: ExecutionPlanNodeOutcome,
    ) -> WorkflowArtifactResult:
        artifact = langgraph_node_result(
            node_id=node.node_id,
            status=outcome.status,
            reason_code=outcome.reason_code,
            value=outcome.value,
            artifacts=outcome.artifacts,
            tokens=outcome.tokens,
            cost_micros=outcome.cost_micros,
            plan_hash=plan.plan_hash,
        )
        succeeded = outcome.status == "completed"
        cancelled = outcome.status == "cancelled"
        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph",
            task_id=task_id,
            task_type=task_type,
            status="success" if succeeded else "blocked" if cancelled else "failed",
            summary=(
                f"LangGraph Hub node {node.node_id} completed."
                if succeeded
                else f"LangGraph Hub node {node.node_id} did not complete."
            ),
            artifacts=[artifact],
            diagnostics=[
                {
                    "schema": "ananta.langgraph_hub_node_diagnostics.v1",
                    "execution_scope": "single_hub_node",
                    "plan_hash": plan.plan_hash,
                    "node_id": node.node_id,
                }
            ],
            execution_trace=[
                *self._audit.snapshot(),
                {
                    "event": f"workflow.step.{outcome.status}",
                    "node_id": node.node_id,
                    "reason_code": outcome.reason_code,
                },
            ],
            policy_decisions=self._policy.decisions_log(),
            error=outcome.reason_code if not succeeded else "",
            reason_code=outcome.reason_code,
        )

    def run(
        self,
        *,
        task_id: str,
        task_type: str,
        payload: dict[str, Any],
        budget: WorkflowBudgetGuard,
    ) -> WorkflowArtifactResult:
        plan = self._load_plan(payload)
        limits = self._parallel_limits(payload)
        approved_gates = self._approved_gates(plan, payload)
        checkpointer = self._checkpointer_provider(task_id=task_id, payload=payload)
        runtime = LangGraphExecutionPlanRuntime(
            node_executor=self._node_executor or _LegacyExecutionPlanNodeExecutor(self)
        )

        def cancel_requested() -> bool:
            if bool(payload.get("cancel_requested", False)):
                return True
            return bool(self._cancel_probe and self._cancel_probe(task_id, dict(payload)))

        runtime_result = runtime.execute(
            plan=plan,
            workflow_input=dict(payload.get("workflow_input") or payload.get("input_data") or {}),
            execution_payload={**payload, "task_id": task_id},
            limits=limits,
            approved_gates=approved_gates,
            cancel_requested=cancel_requested,
            budget=budget,
            checkpointer=checkpointer,
            thread_id=self._checkpoint_thread_id(task_id, payload),
            recursion_limit=self._config.max_iterations,
        )
        artifact_payload = runtime_result.to_artifact()
        artifact_payload.update(
            {
                "artifact_id": _artifact_id(task_id, plan.plan_hash),
                "graph_id": plan.workflow_id,
                "artifact_type": task_type,
            }
        )
        status = {
            "blocked": "blocked",
            "cancelled": "blocked",
            "completed": "success",
            "failed": "failed",
            "partial": "partial",
        }[runtime_result.status]
        summary = (
            f"LangGraph ExecutionPlan {plan.plan_id} finished with {runtime_result.status}; "
            f"{len(runtime_result.records)} nodes, max_parallelism="
            f"{runtime_result.max_observed_parallelism}"
        )
        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph",
            task_id=task_id,
            task_type=task_type,
            status=status,
            summary=summary,
            artifacts=[artifact_payload],
            diagnostics=[
                {
                    "schema": "ananta.langgraph_execution_plan_diagnostics.v1",
                    "plan_hash": plan.plan_hash,
                    "limits": limits.to_dict(),
                    "failed_nodes": runtime_result.failed_nodes,
                    "compiled_components": dict(plan.metadata.get("compiled_components") or {}),
                }
            ],
            execution_trace=[*self._audit.snapshot(), *runtime_result.canonical_trace()],
            policy_decisions=self._policy.decisions_log(),
            error=runtime_result.reason_code if status == "failed" else "",
            reason_code=runtime_result.reason_code,
        )

    def _load_plan(self, payload: dict[str, Any]) -> ExecutionPlan:
        raw_plan = payload.get("execution_plan")
        if not isinstance(raw_plan, dict):
            raise WorkerError("execution_plan_invalid", "execution_plan must be an object.")
        plan = ExecutionPlan.from_mapping(dict(raw_plan))
        if self._component_compiler is not None:
            plan = self._component_compiler.compile(plan)
        elif any(node.node_type == "component" for node in plan.nodes):
            raise WorkerError(
                "workflow_component_compiler_required",
                "Component plans must be compiled by an explicitly configured neutral compiler.",
            )
        bindings = {
            "tenant_id": plan.tenant_id,
            "workflow_id": plan.workflow_id,
            "plan_hash": plan.plan_hash,
            "policy_version": plan.policy_version,
        }
        for name, expected in bindings.items():
            provided = str(payload.get(name) or "")
            if provided and provided != expected:
                raise WorkerError(
                    f"execution_plan_{name}_mismatch",
                    f"Delegated {name} does not match the ExecutionPlan binding.",
                )
        return plan

    def _parallel_limits(self, payload: dict[str, Any]) -> LangGraphParallelLimits:
        raw = payload.get("parallel_limits") or {}
        if not isinstance(raw, dict):
            raise WorkerError("langgraph_parallel_limit_invalid", "parallel_limits must be an object.")
        maximums = {
            "plan": self._config.plan_parallel_limit,
            "tenant": self._config.tenant_parallel_limit,
            "worker": self._config.worker_parallel_limit,
        }
        values: dict[str, int] = {}
        for name, maximum in maximums.items():
            requested = raw.get(name, maximum)
            if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
                raise WorkerError(
                    "langgraph_parallel_limit_invalid",
                    f"parallel_limits.{name} must be a positive integer.",
                )
            if requested > maximum:
                raise WorkerError(
                    "parallel_limit_denied",
                    f"parallel_limits.{name} exceeds the configured worker maximum.",
                )
            values[name] = requested
        limits = LangGraphParallelLimits(**values)
        limits.assert_valid()
        return limits

    @staticmethod
    def _approved_gates(plan: ExecutionPlan, payload: dict[str, Any]) -> frozenset[str]:
        raw = payload.get("approved_gates") or ()
        if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple, set, frozenset)):
            raise WorkerError("approved_gates_invalid", "approved_gates must be a collection.")
        approved = frozenset(str(value).strip() for value in raw if str(value).strip())
        unknown = approved - {gate.gate_id for gate in plan.gates}
        if unknown:
            raise WorkerError(
                "approved_gate_unknown",
                f"Approved gate is not declared by the plan: {','.join(sorted(unknown))}",
            )
        return approved

    def _execute_node(
        self,
        request: ExecutionPlanNodeRequest,
        *,
        budget: WorkflowBudgetGuard,
    ) -> ExecutionPlanNodeOutcome:
        node = request.node
        metadata = dict(node.metadata)
        kind = str(metadata.get("langgraph_kind") or _default_node_kind(node))
        legacy_node = {**metadata, "id": node.node_id, "kind": kind}
        if kind == "tool":
            tool_ref = str(metadata.get("tool_ref") or "")
            if not tool_ref and len(node.allowed_tools) == 1:
                tool_ref = node.allowed_tools[0]
            if tool_ref not in node.allowed_tools:
                raise WorkerError(
                    "execution_plan_tool_scope_mismatch",
                    f"Tool node {node.node_id} is outside its declared tool scope.",
                )
            legacy_node["tool_ref"] = tool_ref
        state = LangGraphState(
            graph_id=request.plan.workflow_id,
            task_id=str(request.execution_payload.get("task_id") or request.plan.plan_id),
            llm_responses=[
                {"node_id": key, "response": value}
                for key, value in sorted(request.dependency_results.items())
            ],
        )
        bound_payload = _bound_payload(request)
        if kind == "llm":
            self._node_operations.invoke_llm(
                node=legacy_node,
                state=state,
                budget=budget,
                execution_payload=bound_payload,
            )
            value = state.llm_responses[-1].get("response") if state.llm_responses else None
        elif kind == "tool":
            self._node_operations.invoke_tool(
                node=legacy_node,
                state=state,
                budget=budget,
                payload=bound_payload,
            )
            value = state.llm_responses[-1].get("result") if state.llm_responses else None
        elif kind == "retriever":
            self._node_operations.invoke_retriever(
                node=legacy_node,
                state=state,
                budget=budget,
                payload=bound_payload,
            )
            value = {"sources": list(state.context_sources)}
        elif kind == "artifact_writer":
            self._node_operations.invoke_artifact_writer(
                node=legacy_node,
                state=state,
                budget=budget,
            )
            value = dict(state.artifacts[-1]) if state.artifacts else {}
        elif node.node_type == "checkpoint":
            value = {
                "checkpoint": (
                    "hub-owned"
                    if self._node_operations.requires_hub_checkpointing()
                    else "ephemeral"
                )
            }
        else:
            raise WorkerError(
                "execution_plan_node_kind_unsupported",
                f"Unsupported neutral plan node kind: {kind}",
            )
        return ExecutionPlanNodeOutcome.completed(value)


class _LegacyExecutionPlanNodeExecutor:
    def __init__(self, adapter: LangGraphExecutionPlanAdapter) -> None:
        self._adapter = adapter

    def execute(
        self,
        request: ExecutionPlanNodeRequest,
        *,
        budget: WorkflowBudgetGuard,
    ) -> ExecutionPlanNodeOutcome:
        return self._adapter._execute_node(request, budget=budget)  # noqa: SLF001


def _bound_payload(request: ExecutionPlanNodeRequest) -> dict[str, Any]:
    payload = dict(request.execution_payload)
    payload.update(
        {
            "tenant_id": request.plan.tenant_id,
            "workflow_id": request.plan.workflow_id,
            "plan_hash": request.plan.plan_hash,
            "policy_version": request.plan.policy_version,
            "step_id": request.node.node_id,
        }
    )
    authorizations = payload.get("node_authorizations") or {}
    if isinstance(authorizations, dict) and isinstance(authorizations.get(request.node.node_id), dict):
        payload["authorization_envelope"] = dict(authorizations[request.node.node_id])
    return payload


def _default_node_kind(node: ExecutionNode) -> str:
    if node.node_type == "checkpoint":
        return "checkpoint"
    if node.task_kind in {"artifact_write", "artifact_writer"} and not node.allowed_tools:
        return "artifact_writer"
    if node.task_kind in {"retrieval", "retriever"}:
        return "retriever"
    if node.task_kind in {"tool", "tool_call"}:
        return "tool"
    return "llm"


def _require_delegated_node(plan: ExecutionPlan, node_id: str) -> ExecutionNode:
    matches = [node for node in plan.nodes if node.node_id == node_id]
    if len(matches) != 1:
        raise WorkerError(
            "langgraph_delegated_node_binding_mismatch",
            "The delegated node is not uniquely bound to the ExecutionPlan.",
        )
    return matches[0]


def _artifact_id(task_id: str, plan_hash: str) -> str:
    digest = hashlib.sha256(f"{task_id}:{plan_hash}".encode()).hexdigest()[:16]
    return f"artifact-lg-plan-{digest}"


__all__ = ["LangGraphExecutionPlanAdapter", "LangGraphPlanNodeOperations"]
