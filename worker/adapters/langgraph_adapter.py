"""LangGraph Worker Adapter (LCG-008, LCG-010, LCG-011, LCG-012, LCG-017, LCG-018, LCG-019, LCG-020, LCG-031..LCG-051).

Optional dependency: langgraph is NOT imported at module load time.
Stateful graph workflows; human gates enforce approval before write/delete/network nodes.
CodeCompass is the only allowed retriever source.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from agent.providers.lc_lg import LangGraphProviderConfig
from agent.services.workflow_runtime.components import WorkflowComponentCompiler
from ananta_contracts.langgraph_checkpoint import LangGraphCheckpointContractError
from ananta_contracts.workflow_fallback import (
    RuntimeFallbackRequest,
    workflow_runtime_fallback_policy,
)
from worker.adapters.chain_runners import SimplexRunner as SimplexRunner
from worker.adapters.langgraph_checkpoint_adapter import (
    LangGraphCheckpointGatewayPort,
    LangGraphHubOwnedCheckpointer,
    binding_from_delegated_payload,
)
from worker.adapters.langgraph_execution_plan import ExecutionPlanNodeExecutor
from worker.adapters.langgraph_execution_plan_adapter import (
    LangGraphExecutionPlanAdapter,
    LangGraphPlanNodeOperations,
)
from worker.adapters.langgraph_legacy_nodes import LangGraphLegacyNodeMixin, LangGraphState
from worker.adapters.langgraph_stream_adapter import LangGraphStreamMixin
from worker.adapters.workflow_adapter_base import (
    DryRunResult,
    WorkerError,
    WorkflowAdapterDescriptor,
    WorkflowArtifactResult,
)
from worker.adapters.workflow_audit import WorkflowAuditLog
from worker.adapters.workflow_budget import WorkflowBudgetGuard
from worker.adapters.workflow_policy_gate import WorkflowPolicyGate
from worker.core.tool_calling_pipeline import ToolCallingPipeline
from worker.retrieval.codecompass_retriever import (
    CodeCompassRetriever,
    retrieval_request_from_payload,
)

_SUPPORTED_TASK_TYPES = frozenset(
    {
        "agent_workflow",
        "multi_step_plan",
        "human_in_loop",
        "stateful_task",
        "review_workflow",
    }
)

_RISK_MAP = {
    "agent_workflow": "high",
    "multi_step_plan": "medium",
    "human_in_loop": "medium",
    "stateful_task": "high",
    "review_workflow": "low",
}

class LangGraphAdapter(LangGraphLegacyNodeMixin, LangGraphStreamMixin):
    """Optional LangGraph worker adapter."""

    def __init__(
        self,
        config: LangGraphProviderConfig | None = None,
        *,
        tool_pipeline: ToolCallingPipeline | None = None,
        checkpoint_gateway: LangGraphCheckpointGatewayPort | None = None,
        execution_plan_node_executor: ExecutionPlanNodeExecutor | None = None,
        component_compiler: WorkflowComponentCompiler | None = None,
        execution_plan_cancel_probe: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self._config = config or LangGraphProviderConfig.default_off()
        self._tool_pipeline = tool_pipeline
        self._checkpoint_gateway = checkpoint_gateway
        self._policy = WorkflowPolicyGate(
            external_calls_allowed=self._config.external_calls_allowed,
            allowed_tools=set(self._config.allowed_tools),
            human_required_actions=set(self._config.human_in_loop_required_for),
        )
        self._audit = WorkflowAuditLog(adapter_id="adapter.langgraph")
        # LCG-032: use embedding_provider_scope from config instead of hardcode
        self._retriever = CodeCompassRetriever(
            scope=self._config.embedding_provider_scope,
        )
        self._execution_plans = LangGraphExecutionPlanAdapter(
            config=self._config,
            audit=self._audit,
            policy=self._policy,
            node_operations=LangGraphPlanNodeOperations(
                invoke_llm=self._invoke_llm_node,
                invoke_tool=self._invoke_tool_node,
                invoke_retriever=self._invoke_retriever_node,
                invoke_artifact_writer=self._invoke_artifact_writer_node,
                requires_hub_checkpointing=self._requires_hub_checkpointing,
            ),
            checkpointer_provider=self._get_checkpointer,
            checkpoint_thread_id=self._checkpoint_thread_id,
            node_executor=execution_plan_node_executor,
            component_compiler=component_compiler,
            cancel_probe=execution_plan_cancel_probe,
        )

    def descriptor(self) -> WorkflowAdapterDescriptor:
        available = self._langgraph_available()
        enabled = self._config.enabled and available
        if not self._config.enabled:
            status, reason = "disabled", "adapter_disabled_by_config"
        elif not available:
            status, reason = "degraded", "langgraph_not_installed"
        elif not self._production_checkpoint_policy_valid():
            status, reason = "degraded", "production_checkpoint_policy_invalid"
            enabled = False
        elif self._requires_hub_checkpointing() and self._checkpoint_gateway is None:
            status, reason = "degraded", "hub_checkpoint_gateway_required"
            enabled = False
        elif self._config.mode == "dry_run":
            status, reason = "ready", "dry_run_mode"
        else:
            status, reason = "ready", "ready"
        # LCG-033: provider diagnostics
        model_ref = self._config.model_provider_ref
        locality = "cloud" if self._config.mode == "cloud_gated" else "local"
        provider_diagnostics = {
            "model_ref": model_ref,
            "locality": locality,
            "external_calls": self._config.external_calls_allowed,
            "checkpoint_policy": self._config.checkpoint_policy,
        }
        return WorkflowAdapterDescriptor(
            adapter_id="adapter.langgraph",
            display_name="LangGraph",
            kind="langgraph",
            status=status,  # type: ignore[arg-type]
            enabled=enabled,
            reason=reason,
            capabilities=[
                "dry_run",
                "agent_workflow",
                "multi_step_plan",
                "human_in_loop",
                "stateful_task",
                "review_workflow",
                "checkpointing",
                "codecompass_retriever",
            ],
            version="1.0.0",
            provider_diagnostics=provider_diagnostics,
        )

    # ── Dry-run ────────────────────────────────────────────────────────────────

    def dry_run(self, *, task_id: str, task_type: str, payload: dict[str, Any]) -> DryRunResult:
        self._audit.snapshot()
        self._audit.log("dry_run_start", task_id=task_id, task_type=task_type)
        result = DryRunResult(
            adapter_id="adapter.langgraph",
            task_id=task_id,
            task_type=task_type,
            risk_level=_RISK_MAP.get(task_type, "high"),
        )

        if task_type not in _SUPPORTED_TASK_TYPES:
            result.blocked = True
            result.block_reason = f"unsupported_task_type:{task_type}"
            self._audit.log("dry_run_blocked", task_id=task_id, reason=result.block_reason)
            return result

        graph_id = str(payload.get("graph_id") or "")
        if graph_id and not self._config.graph_allowed(graph_id):
            result.blocked = True
            result.block_reason = f"graph_not_in_allowlist:{graph_id}"
            self._audit.log("dry_run_blocked", task_id=task_id, reason=result.block_reason)
            return result

        if payload.get("execution_plan") is not None:
            return self._execution_plans.dry_run(
                task_id=task_id,
                task_type=task_type,
                payload=payload,
                result=result,
            )

        descriptor = payload.get("graph_descriptor") or {}
        nodes = descriptor.get("nodes") or []

        # LCG-046: max_nodes enforcement in dry_run
        if len(nodes) > self._config.max_nodes:
            result.blocked = True
            result.block_reason = f"graph_too_many_nodes:{len(nodes)}/{self._config.max_nodes}"
            self._audit.log("dry_run_blocked", task_id=task_id, reason=result.block_reason)
            return result

        human_gate_nodes = [n["id"] for n in nodes if n.get("kind") == "human_gate"]
        high_risk_nodes = [
            n["id"] for n in nodes if n.get("kind") == "tool" and self._policy.requires_human(n.get("tool_ref", ""))
        ]

        retriever = payload.get("retriever_ref") or "none"
        if retriever and retriever != "none" and retriever != "codecompass":
            result.blocked = True
            result.block_reason = "only_codecompass_retriever_allowed"
        if retriever == "codecompass":
            result.required_context_sources.append("codecompass")

        if payload.get("external_url") and not self._config.external_calls_allowed:
            result.blocked = True
            result.block_reason = "external_calls_blocked_by_policy"

        result.plan_steps = self._build_plan(task_type, nodes, retriever)
        result.estimated_tokens = _estimate_tokens(payload, nodes)

        if not result.blocked:
            needs_approval = bool(human_gate_nodes or high_risk_nodes)
            result.approval_required = needs_approval
            if needs_approval:
                result.approval_reasons = [f"human_gate_node:{n}" for n in human_gate_nodes] + [
                    f"high_risk_tool_node:{n}" for n in high_risk_nodes
                ]

        self._audit.log(
            "dry_run_complete", task_id=task_id, blocked=result.blocked, approval_required=result.approval_required
        )
        result.metadata["dry_run_audit_trace"] = self._audit.snapshot()
        return result

    # ── Live execute ───────────────────────────────────────────────────────────

    def execute(
        self, *, task_id: str, task_type: str, payload: dict[str, Any], resume_token: str | None = None
    ) -> WorkflowArtifactResult:
        self._audit.snapshot()
        self._audit.log("execute_start", task_id=task_id, task_type=task_type)

        if not self._config.is_live():
            return self._blocked_result(
                task_id,
                task_type,
                "live_execution_requires_live_mode",
                "Adapter is in dry_run mode; set mode=local_live to execute.",
            )

        if not self._production_checkpoint_policy_valid():
            return self._blocked_result(
                task_id,
                task_type,
                "production_checkpoint_policy_invalid",
                "Production LangGraph profiles require Hub-owned checkpointing.",
            )

        if resume_token is not None:
            if not self._legacy_resume_tokens_allowed():
                return self._blocked_result(
                    task_id,
                    task_type,
                    "unsigned_resume_token_forbidden",
                    "Unsigned JSON resume tokens are restricted to explicit ephemeral Dev mode.",
                )
            return self._resume_from_token(task_id, task_type, payload, resume_token)

        dry = self.dry_run(task_id=task_id, task_type=task_type, payload=payload)
        if dry.blocked:
            return self._blocked_result(task_id, task_type, dry.block_reason, f"blocked by dry-run: {dry.block_reason}")

        if dry.approval_required:
            return self._blocked_result(
                task_id,
                task_type,
                "human_approval_required",
                f"Human approval required: {'; '.join(dry.approval_reasons)}",
            )

        budget = WorkflowBudgetGuard(
            max_steps=self._config.max_iterations,
            timeout_seconds=self._config.timeout_seconds,
            max_tokens=self._config.max_tokens,
        )

        if self._requires_hub_checkpointing():
            if not self._langgraph_available():
                return self._blocked_result(
                    task_id,
                    task_type,
                    "langgraph_required_for_hub_checkpointing",
                    "Hub-owned checkpointing requires the LangGraph runtime extra.",
                )
            try:
                self._get_checkpointer(task_id=task_id, payload=payload)
            except WorkerError as exc:
                return self._blocked_result(task_id, task_type, exc.reason_code, str(exc))

        try:
            result = self._run_graph(task_id, task_type, payload, budget)
        except WorkerError as exc:
            self._audit.log("execute_failed", task_id=task_id, reason_code=exc.reason_code)
            return WorkflowArtifactResult(
                adapter_id="adapter.langgraph",
                task_id=task_id,
                task_type=task_type,
                status="failed",
                summary=str(exc),
                error=str(exc),
                reason_code=exc.reason_code,
                execution_trace=self._audit.snapshot(),
                policy_decisions=self._policy.decisions_log(),
            )

        self._audit.log("execute_complete", task_id=task_id, status=result.status)
        return result

    # ── Internal ───────────────────────────────────────────────────────────────

    def _run_graph(
        self, task_id: str, task_type: str, payload: dict[str, Any], budget: WorkflowBudgetGuard, _depth: int = 0
    ) -> WorkflowArtifactResult:
        state = LangGraphState(
            graph_id=str(payload.get("graph_id") or f"graph-{task_type}"),
            task_id=task_id,
        )

        if payload.get("execution_plan") is not None:
            if _depth != 0:
                raise WorkerError(
                    "nested_execution_plan_forbidden",
                    "Neutral ExecutionPlans must be flattened before worker execution.",
                )
            if not self._langgraph_available():
                raise WorkerError(
                    "langgraph_not_installed",
                    "ExecutionPlan execution requires the pinned LangGraph runtime extra.",
                )
            if payload.get("execution_scope") == "single_hub_node":
                return self._execution_plans.run_delegated_node(
                    task_id=task_id,
                    task_type=task_type,
                    payload=payload,
                    budget=budget,
                )
            # Kept for the framework's isolated conformance harness. The
            # production Worker consumer rejects this shape before dispatch.
            return self._execution_plans.run(
                task_id=task_id,
                task_type=task_type,
                payload=payload,
                budget=budget,
            )

        retriever = payload.get("retriever_ref") or "none"
        if retriever == "codecompass":
            query = str(payload.get("query") or payload.get("prompt") or "")
            if query:
                request = retrieval_request_from_payload(
                    query=query,
                    payload=payload,
                    default_scope=self._config.embedding_provider_scope,
                    max_results=5,
                )
                ctx = self._retriever.retrieve(request).to_dict()
                state.context_sources = ctx.get("sources", [])
                budget.record_step("codecompass_retrieval")

        descriptor = payload.get("graph_descriptor") or {}
        nodes = descriptor.get("nodes") or []
        edges = descriptor.get("edges") or {}
        stop_conditions = descriptor.get("stop_conditions") or {}
        max_iter = min(
            stop_conditions.get("max_iterations", self._config.max_iterations),
            self._config.max_iterations,
        )

        # LCG-046: max_nodes check at runtime too
        if nodes and len(nodes) > self._config.max_nodes:
            raise WorkerError(
                "graph_too_many_nodes",
                f"Graph has {len(nodes)} nodes, max_nodes={self._config.max_nodes}",
            )

        # LCG-047: framework-backed execution is mandatory outside an explicit
        # development-only compatibility profile.  Missing dependencies must
        # never silently change production semantics.
        langgraph_available = self._langgraph_available()
        if _depth == 0 and not langgraph_available:
            decision = self._manual_walker_fallback_decision(
                payload,
                reason_code="langgraph_not_installed",
            )
            self._audit.log(
                "langgraph_dependency_fallback_evaluated",
                task_id=task_id,
                **decision.to_dict(),
            )
            if not decision.allowed:
                raise WorkerError(
                    "langgraph_not_installed",
                    "LangGraph live execution requires the pinned LangGraph runtime extra.",
                    details=decision.to_dict(),
                )

        if nodes and langgraph_available and _depth == 0:
            try:
                return self._run_compiled_graph(task_id, task_type, payload, budget)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, WorkerError):
                    raise
                if self._requires_hub_checkpointing():
                    raise WorkerError(
                        "hub_checkpoint_execution_failed",
                        "Hub-owned LangGraph execution failed; ephemeral fallback is forbidden.",
                    ) from exc
                decision = self._manual_walker_fallback_decision(
                    payload,
                    reason_code="compiled_graph_execution_failed",
                )
                self._audit.log(
                    "compiled_graph_fallback_evaluated",
                    task_id=task_id,
                    exception_type=type(exc).__name__,
                    **decision.to_dict(),
                )
                if not decision.allowed:
                    raise WorkerError(
                        "compiled_graph_fallback_blocked",
                        "Compiled LangGraph execution failed and no semantics-preserving fallback is allowed.",
                        details=decision.to_dict(),
                    ) from exc

        if nodes:
            self._walk_nodes(nodes, edges, state, budget, max_iter, payload=payload, _depth=_depth)
        else:
            state.nodes_visited = ["llm", "artifact_writer", "end"]
            state.stopped_at = "end"
            state.stop_reason = "end_node"
            budget.record_step("llm_call")
            budget.record_step("artifact_write")

        # LCG-049: produce resume_token if stopped at human_gate
        resume_token: str | None = None
        if state.stop_reason == "human_gate" and self._legacy_resume_tokens_allowed():
            resume_token = self._serialize_state(state)

        artifact_id = f"artifact-lg-{uuid.uuid4().hex[:12]}"
        artifact = {
            "artifact_id": artifact_id,
            "graph_id": state.graph_id,
            "artifact_type": task_type,
            "status": "created",
            "nodes_visited": state.nodes_visited,
            "stop_reason": state.stop_reason,
            "context_sources_count": len(state.context_sources),
            "iterations": state.iteration,
        }
        state.artifacts.append(artifact)

        final_status = "blocked" if state.stop_reason == "human_gate" else "success"

        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph",
            task_id=task_id,
            task_type=task_type,
            status=final_status,
            summary=(
                f"LangGraph {task_type} ({state.graph_id}) completed "
                f"in {state.iteration} iterations, {len(state.nodes_visited)} nodes visited"
            ),
            artifacts=state.artifacts,
            sources=state.context_sources,
            execution_trace=self._audit.snapshot(),
            policy_decisions=self._policy.decisions_log(),
            resume_token=resume_token,
            reason_code="human_gate" if state.stop_reason == "human_gate" else "",
        )

    def _manual_walker_fallback_decision(
        self,
        payload: dict[str, Any],
        *,
        reason_code: str,
    ):
        descriptor = payload.get("graph_descriptor") or {}
        nodes = list(descriptor.get("nodes") or [])
        source_capabilities = {
            "policy",
            "audit",
            "graph_execution",
            "side_effect_guard",
            "conditional_routing",
        }
        target_capabilities = set(source_capabilities)
        if self._requires_hub_checkpointing():
            source_capabilities.add("durability")
        if any(node.get("kind") == "human_gate" for node in nodes):
            source_capabilities.add("resume")
            if self._legacy_resume_tokens_allowed():
                target_capabilities.add("resume")
        semantic_class = "equivalent" if source_capabilities == target_capabilities else "degraded"
        metadata = self._config.metadata or {}
        explicit_policy = bool(
            metadata.get("allow_manual_walker_fallback", False)
            and metadata.get("manual_walker_fallback_mode") == "development"
            and not self._requires_hub_checkpointing()
        )
        return workflow_runtime_fallback_policy.evaluate(
            RuntimeFallbackRequest.create(
                source_runtime="langgraph.compiled",
                target_runtime="langgraph.manual_walker",
                reason_code=reason_code,
                semantic_class=semantic_class,
                source_capabilities=source_capabilities,
                target_capabilities=target_capabilities,
                explicitly_enabled=explicit_policy,
            )
        )

    def _blocked_result(self, task_id: str, task_type: str, reason_code: str, message: str) -> WorkflowArtifactResult:
        self._audit.log("execute_blocked", task_id=task_id, reason_code=reason_code)
        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph",
            task_id=task_id,
            task_type=task_type,
            status="blocked",
            summary=message,
            error=message,
            reason_code=reason_code,
            execution_trace=self._audit.snapshot(),
        )

    def _serialize_state(self, state: LangGraphState) -> str:
        # LCG-049: serialize state for resume_token; apply redaction to llm_responses
        try:
            from ananta_contracts.redaction import redact

            _redact = redact
        except ImportError:
            _redact = str
        safe_responses = [
            {k: _redact(str(v)) if isinstance(v, str) else v for k, v in r.items()} for r in state.llm_responses
        ]
        token_data = {
            "schema": "ananta.langgraph_local_resume.v1",
            "graph_id": state.graph_id,
            "task_id": state.task_id,
            "stopped_at": state.stopped_at,
            "stop_reason": state.stop_reason,
            "nodes_visited": state.nodes_visited,
            "iteration": state.iteration,
            "llm_responses": safe_responses,
        }
        return json.dumps(token_data)

    def _resume_from_token(
        self, task_id: str, task_type: str, payload: dict[str, Any], resume_token: str
    ) -> WorkflowArtifactResult:
        # LCG-049: resume execution from serialized state
        try:
            token_data = json.loads(resume_token)
        except (ValueError, TypeError):
            self._audit.log("resume_token_invalid", task_id=task_id)
            return WorkflowArtifactResult(
                adapter_id="adapter.langgraph",
                task_id=task_id,
                task_type=task_type,
                status="failed",
                summary="Invalid resume_token",
                error="resume_token could not be parsed",
                reason_code="resume_token_invalid",
                execution_trace=self._audit.snapshot(),
            )
        if (
            not isinstance(token_data, dict)
            or token_data.get("schema") != "ananta.langgraph_local_resume.v1"
            or token_data.get("task_id") != task_id
            or len(resume_token.encode("utf-8")) > 65_536
        ):
            return self._blocked_result(
                task_id,
                task_type,
                "resume_token_binding_mismatch",
                "Local resume token does not match the delegated task.",
            )

        self._audit.log("resume_start", task_id=task_id, stopped_at=token_data.get("stopped_at"))

        budget = WorkflowBudgetGuard(
            max_steps=self._config.max_iterations,
            timeout_seconds=self._config.timeout_seconds,
            max_tokens=self._config.max_tokens,
        )

        # Restore state and continue from the node AFTER the stopped_at node
        descriptor = payload.get("graph_descriptor") or {}
        nodes = descriptor.get("nodes") or []
        edges = descriptor.get("edges") or []
        stopped_at = token_data.get("stopped_at", "")

        # Find the next node after the human_gate
        next_node = None
        for e in edges:
            if e.get("from") == stopped_at:
                next_node = e.get("to")
                break

        if not next_node:
            return WorkflowArtifactResult(
                adapter_id="adapter.langgraph",
                task_id=task_id,
                task_type=task_type,
                status="failed",
                summary="Cannot resume: no outbound edge from stopped_at node",
                error=f"No outbound edge from {stopped_at}",
                reason_code="resume_no_outbound_edge",
                execution_trace=self._audit.snapshot(),
            )

        state = LangGraphState(
            graph_id=token_data.get("graph_id", f"graph-{task_type}"),
            task_id=task_id,
            nodes_visited=list(token_data.get("nodes_visited", [])),
            iteration=int(token_data.get("iteration", 0)),
            llm_responses=list(token_data.get("llm_responses", [])),
        )

        # Rebuild nodes list starting from next_node
        node_map = {n["id"]: n for n in nodes}
        seen = set(state.nodes_visited)
        visited_order = []
        # Simple BFS to get remaining nodes in edge order
        cur = next_node
        while cur and cur not in seen:
            visited_order.append(cur)
            seen.add(cur)
            nxt = None
            for e in edges:
                if e.get("from") == cur:
                    nxt = e.get("to")
                    break
            cur = nxt
        resume_nodes = [node_map[n] for n in visited_order if n in node_map]

        max_iter = self._config.max_iterations
        self._walk_nodes(resume_nodes, edges, state, budget, max_iter, payload=payload, _depth=0)

        artifact_id = f"artifact-lg-resume-{uuid.uuid4().hex[:8]}"
        artifact = {
            "artifact_id": artifact_id,
            "graph_id": state.graph_id,
            "artifact_type": task_type,
            "status": "created",
            "nodes_visited": state.nodes_visited,
            "stop_reason": state.stop_reason,
            "resumed_from": stopped_at,
        }
        state.artifacts.append(artifact)

        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph",
            task_id=task_id,
            task_type=task_type,
            status="success",
            summary=f"LangGraph resumed from {stopped_at}, completed with stop_reason={state.stop_reason}",
            artifacts=state.artifacts,
            sources=state.context_sources,
            execution_trace=self._audit.snapshot(),
            policy_decisions=self._policy.decisions_log(),
        )

    # ── LCG-047: StateGraph.compile() live path ────────────────────────────────

    def _run_compiled_graph(
        self, task_id: str, task_type: str, payload: dict[str, Any], budget: WorkflowBudgetGuard
    ) -> WorkflowArtifactResult:
        """Optional live path using StateGraph.compile() when langgraph is installed.

        Builds a real LangGraph StateGraph from graph_descriptor, compiles it
        with an optional checkpointer, and invokes it. Falls back to _walk_nodes
        on any error.
        """
        from langgraph.graph import END, StateGraph  # type: ignore[import]

        descriptor = payload.get("graph_descriptor") or {}
        nodes = descriptor.get("nodes") or []
        edges = descriptor.get("edges") or []
        state_obj = LangGraphState(
            graph_id=str(payload.get("graph_id") or f"graph-{task_type}"),
            task_id=task_id,
        )

        # Build adapter node functions that call into our existing node implementations
        def _make_node_fn(node: dict) -> Any:
            def _node_fn(lg_state: dict) -> dict:
                budget.record_step(f"node:{node.get('id', '?')}")
                kind = node.get("kind", "llm")
                self._audit.log("node_enter", task_id=task_id, node=node.get("id", ""), kind=kind)
                if kind == "llm":
                    self._invoke_llm_node(
                        node=node,
                        state=state_obj,
                        budget=budget,
                        execution_payload=payload,
                    )
                elif kind == "tool":
                    self._invoke_tool_node(
                        node=node,
                        state=state_obj,
                        budget=budget,
                        payload=payload,
                    )
                elif kind == "retriever":
                    self._invoke_retriever_node(
                        node=node,
                        state=state_obj,
                        budget=budget,
                        payload=payload,
                    )
                elif kind == "artifact_writer":
                    self._invoke_artifact_writer_node(node=node, state=state_obj, budget=budget)
                elif kind == "human_gate":
                    state_obj.stopped_at = node.get("id", "")
                    state_obj.stop_reason = "human_gate"
                    self._audit.log("human_gate_stop", task_id=task_id, node=node.get("id", ""))
                state_obj.nodes_visited.append(node.get("id", ""))
                state_obj.iteration += 1
                return lg_state

            return _node_fn

        # Build StateGraph
        graph_builder: Any = StateGraph(dict)
        node_ids = set()
        for node in nodes:
            nid = node.get("id", "")
            if not nid or node.get("kind") == "end":
                continue
            graph_builder.add_node(nid, _make_node_fn(node))
            node_ids.add(nid)

        if nodes:
            first_id = nodes[0].get("id", "")
            if first_id and first_id in node_ids:
                graph_builder.set_entry_point(first_id)

        for edge in edges:
            frm = edge.get("from", "")
            to = edge.get("to", "")
            if frm in node_ids:
                if to == "end" or to not in node_ids:
                    graph_builder.add_edge(frm, END)
                else:
                    graph_builder.add_edge(frm, to)

        checkpointer = self._get_checkpointer(task_id=task_id, payload=payload)
        compiled = graph_builder.compile(checkpointer=checkpointer)

        compiled.invoke(
            {},
            config={
                "recursion_limit": self._config.max_iterations,
                "configurable": {
                    "thread_id": self._checkpoint_thread_id(task_id, payload),
                    "checkpoint_ns": "",
                },
            },
        )

        artifact_id = f"artifact-lg-compiled-{uuid.uuid4().hex[:12]}"
        artifact = {
            "artifact_id": artifact_id,
            "graph_id": state_obj.graph_id,
            "artifact_type": task_type,
            "status": "created",
            "nodes_visited": state_obj.nodes_visited,
            "stop_reason": state_obj.stop_reason or "end_node",
            "context_sources_count": len(state_obj.context_sources),
            "iterations": state_obj.iteration,
            "compiled_graph": True,
        }
        state_obj.artifacts.append(artifact)

        final_status = "blocked" if state_obj.stop_reason == "human_gate" else "success"
        resume_token: str | None = None
        if state_obj.stop_reason == "human_gate" and self._legacy_resume_tokens_allowed():
            resume_token = self._serialize_state(state_obj)

        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph",
            task_id=task_id,
            task_type=task_type,
            status=final_status,
            summary=(
                f"LangGraph compiled {task_type} ({state_obj.graph_id}) completed in {state_obj.iteration} iterations"
            ),
            artifacts=state_obj.artifacts,
            sources=state_obj.context_sources,
            execution_trace=self._audit.snapshot(),
            policy_decisions=self._policy.decisions_log(),
            resume_token=resume_token,
            reason_code="human_gate" if state_obj.stop_reason == "human_gate" else "",
        )

    # ── LCG-048: Checkpointing ─────────────────────────────────────────────────

    def _get_checkpointer(
        self,
        *,
        task_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Return a LangGraph checkpointer based on checkpoint_policy config.

        ``hub_owned`` is fail-closed: it never degrades to MemorySaver.
        """
        policy = self._config.checkpoint_policy
        if self._requires_hub_checkpointing():
            if policy not in {"hub_owned", "local_ephemeral_or_hub_owned"}:
                raise WorkerError(
                    "production_checkpoint_policy_invalid",
                    "Production LangGraph profiles require Hub-owned checkpointing.",
                )
            return self._hub_owned_checkpointer(task_id=task_id, payload=payload)
        if policy == "none":
            return None
        if policy == "hub_owned":
            return self._hub_owned_checkpointer(task_id=task_id, payload=payload)
        if policy == "local_ephemeral_or_hub_owned" and self._checkpoint_gateway is not None:
            return self._hub_owned_checkpointer(task_id=task_id, payload=payload)
        if policy in ("local_ephemeral", "local_ephemeral_or_hub_owned"):
            try:
                from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]

                return MemorySaver()
            except ImportError:
                return None
        return None

    def _hub_owned_checkpointer(
        self,
        *,
        task_id: str,
        payload: dict[str, Any] | None,
    ) -> LangGraphHubOwnedCheckpointer:
        if self._checkpoint_gateway is None:
            raise WorkerError(
                "hub_checkpoint_gateway_required",
                "Hub-owned checkpoint policy requires the internal checkpoint gateway.",
            )
        if not task_id or payload is None:
            raise WorkerError(
                "hub_checkpoint_binding_required",
                "Hub-owned checkpoint policy requires a delegated task binding.",
            )
        try:
            binding = binding_from_delegated_payload(task_id=task_id, payload=payload)
        except LangGraphCheckpointContractError as exc:
            raise WorkerError(exc.reason_code, "Invalid Hub checkpoint task binding.") from exc
        return LangGraphHubOwnedCheckpointer(
            gateway=self._checkpoint_gateway,
            binding=binding,
        )

    def _legacy_resume_tokens_allowed(self) -> bool:
        return bool(self._config.checkpoint_policy == "local_ephemeral" and self._config.state_policy == "ephemeral")

    def _requires_hub_checkpointing(self) -> bool:
        return bool(
            self._config.checkpoint_policy == "hub_owned"
            or self._config.state_policy == "hub_owned"
            or self._config.mode == "cloud_gated"
        )

    def _production_checkpoint_policy_valid(self) -> bool:
        if self._config.state_policy != "hub_owned" and self._config.mode != "cloud_gated":
            return True
        return self._config.checkpoint_policy in {
            "hub_owned",
            "local_ephemeral_or_hub_owned",
        }

    def _checkpoint_thread_id(self, task_id: str, payload: dict[str, Any]) -> str:
        if self._requires_hub_checkpointing() or (
            self._config.checkpoint_policy == "local_ephemeral_or_hub_owned" and self._checkpoint_gateway is not None
        ):
            return str(payload.get("step_id") or "")
        return str(task_id)

    # ── LCG-050: stream() ──────────────────────────────────────────────────────

    @staticmethod
    def _langgraph_available() -> bool:
        try:
            import importlib

            importlib.import_module("langgraph")
            return True
        except ImportError:
            return False


def _estimate_tokens(payload: dict[str, Any], nodes: list[dict]) -> int:
    text = str(payload.get("query") or payload.get("prompt") or "")
    return max(200, len(text) // 4 + len(nodes) * 150 + 300)
