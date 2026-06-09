"""LangGraph Worker Adapter (LCG-008, LCG-010, LCG-011, LCG-012, LCG-017, LCG-018, LCG-019, LCG-020).

Optional dependency: langgraph is NOT imported at module load time.
Stateful graph workflows; human gates enforce approval before write/delete/network nodes.
CodeCompass is the only allowed retriever source.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.providers.lc_lg import LangGraphProviderConfig
from worker.adapters.workflow_adapter_base import (
    DryRunResult, WorkerError, WorkflowAdapterDescriptor, WorkflowArtifactResult,
)
from worker.adapters.workflow_policy_gate import WorkflowPolicyGate
from worker.adapters.workflow_audit import WorkflowAuditLog
from worker.adapters.workflow_budget import WorkflowBudgetGuard
from worker.retrieval.codecompass_retriever import CodeCompassRetriever


_SUPPORTED_TASK_TYPES = frozenset({
    "agent_workflow", "multi_step_plan", "human_in_loop", "stateful_task", "review_workflow",
})

_RISK_MAP = {
    "agent_workflow": "high",
    "multi_step_plan": "medium",
    "human_in_loop": "medium",
    "stateful_task": "high",
    "review_workflow": "low",
}


@dataclass
class _GraphState:
    graph_id: str
    task_id: str
    nodes_visited: list[str] = field(default_factory=list)
    current_node: str = ""
    context_sources: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    stopped_at: str = ""
    stop_reason: str = ""


class LangGraphAdapter:
    """Optional LangGraph worker adapter.

    Respects LangGraphProviderConfig.  Stateful graph runs require explicit
    live mode.  Human gates block automatically on any write/delete/network node.
    """

    def __init__(self, config: LangGraphProviderConfig | None = None) -> None:
        self._config = config or LangGraphProviderConfig.default_off()
        self._policy = WorkflowPolicyGate(
            external_calls_allowed=self._config.external_calls_allowed,
            allowed_tools=set(),
            human_required_actions=set(self._config.human_in_loop_required_for),
        )
        self._audit = WorkflowAuditLog(adapter_id="adapter.langgraph")
        # LCG-010: wire the retriever to the same scope the LangGraph
        # side uses for embedding selection. The scope is hard-coded
        # to "codecompass_vector" for now (matches the LangChain side
        # and the existing CodeCompassVectorEngine); a future
        # embedding_provider_scope field on LangGraphProviderConfig
        # can override it without touching this line.
        self._retriever = CodeCompassRetriever(scope="codecompass_vector")

    def descriptor(self) -> WorkflowAdapterDescriptor:
        available = self._langgraph_available()
        enabled = self._config.enabled and available
        if not self._config.enabled:
            status, reason = "disabled", "adapter_disabled_by_config"
        elif not available:
            status, reason = "degraded", "langgraph_not_installed"
        elif self._config.mode == "dry_run":
            status, reason = "ready", "dry_run_mode"
        else:
            status, reason = "ready", "ready"
        return WorkflowAdapterDescriptor(
            adapter_id="adapter.langgraph",
            display_name="LangGraph",
            kind="langgraph",
            status=status,  # type: ignore[arg-type]
            enabled=enabled,
            reason=reason,
            capabilities=[
                "dry_run", "agent_workflow", "multi_step_plan",
                "human_in_loop", "stateful_task", "review_workflow",
                "checkpointing", "codecompass_retriever",
            ],
            version="1.0",
        )

    # ── Dry-run (LCG-017) ─────────────────────────────────────────────────────

    def dry_run(self, *, task_id: str, task_type: str,
                 payload: dict[str, Any]) -> DryRunResult:
        # Discard the previous task's events so this task's audit log
        # starts fresh. dry_run's own events are captured below before
        # return and attached to the result.
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

        # Analyse graph descriptor (LCG-015)
        descriptor = payload.get("graph_descriptor") or {}
        nodes = descriptor.get("nodes") or []
        human_gate_nodes = [n["id"] for n in nodes if n.get("kind") == "human_gate"]
        high_risk_nodes = [
            n["id"] for n in nodes
            if n.get("kind") == "tool" and self._policy.requires_human(n.get("tool_ref", ""))
        ]

        # External calls
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
                result.approval_reasons = (
                    [f"human_gate_node:{n}" for n in human_gate_nodes]
                    + [f"high_risk_tool_node:{n}" for n in high_risk_nodes]
                )

        self._audit.log("dry_run_complete", task_id=task_id,
                         blocked=result.blocked, approval_required=result.approval_required)
        # Attach this task's events to the result, then clear for the
        # next task.
        result.metadata["dry_run_audit_trace"] = self._audit.snapshot()
        return result

    # ── Live execute (LCG-008) ────────────────────────────────────────────────

    def execute(self, *, task_id: str, task_type: str,
                 payload: dict[str, Any]) -> WorkflowArtifactResult:
        # Atomic snapshot so execute gets a clean audit log even if
        # dry_run was called first. dry_run's events are already in
        # metadata.dry_run_audit_trace of the DryRunResult.
        self._audit.snapshot()
        self._audit.log("execute_start", task_id=task_id, task_type=task_type)

        if not self._config.is_live():
            return self._blocked_result(
                task_id, task_type,
                "live_execution_requires_live_mode",
                "Adapter is in dry_run mode; set mode=local_live to execute.",
            )

        dry = self.dry_run(task_id=task_id, task_type=task_type, payload=payload)
        if dry.blocked:
            return self._blocked_result(task_id, task_type, dry.block_reason,
                                         f"blocked by dry-run: {dry.block_reason}")

        if dry.approval_required:
            return self._blocked_result(
                task_id, task_type, "human_approval_required",
                f"Human approval required: {'; '.join(dry.approval_reasons)}",
            )

        budget = WorkflowBudgetGuard(
            max_steps=self._config.max_iterations,
            timeout_seconds=self._config.timeout_seconds,
            max_tokens=self._config.max_tokens,
        )

        try:
            result = self._run_graph(task_id, task_type, payload, budget)
        except WorkerError as exc:
            self._audit.log("execute_failed", task_id=task_id, reason_code=exc.reason_code)
            return WorkflowArtifactResult(
                adapter_id="adapter.langgraph", task_id=task_id, task_type=task_type,
                status="failed", summary=str(exc), error=str(exc), reason_code=exc.reason_code,
            )

        self._audit.log("execute_complete", task_id=task_id, status=result.status)
        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_graph(self, task_id: str, task_type: str,
                    payload: dict[str, Any], budget: WorkflowBudgetGuard) -> WorkflowArtifactResult:
        if not self._langgraph_available():
            raise WorkerError("langgraph_not_installed",
                               "langgraph package is not installed; pip install ananta[langgraph]")

        state = _GraphState(
            graph_id=str(payload.get("graph_id") or f"graph-{task_type}"),
            task_id=task_id,
        )

        # Retrieve CodeCompass context (LCG-009, LCG-010)
        retriever = payload.get("retriever_ref") or "none"
        if retriever == "codecompass":
            query = str(payload.get("query") or payload.get("prompt") or "")
            if query:
                ctx = self._retriever.query(query, max_results=5)
                state.context_sources = ctx.get("sources", [])
                budget.record_step("codecompass_retrieval")

        # Walk declared graph nodes (dry simulation of the LangGraph execution)
        descriptor = payload.get("graph_descriptor") or {}
        nodes = descriptor.get("nodes") or []
        edges = descriptor.get("edges") or {}
        stop_conditions = descriptor.get("stop_conditions") or {}
        max_iter = min(
            stop_conditions.get("max_iterations", self._config.max_iterations),
            self._config.max_iterations,
        )

        if nodes:
            self._walk_nodes(nodes, edges, state, budget, max_iter)
        else:
            # No descriptor — minimal execution stub
            state.nodes_visited = ["llm", "artifact_writer", "end"]
            state.stopped_at = "end"
            state.stop_reason = "end_node"
            budget.record_step("llm_call")
            budget.record_step("artifact_write")

        # Artifact-first output (LCG-013)
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

        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph",
            task_id=task_id,
            task_type=task_type,
            status="success",
            summary=(
                f"LangGraph {task_type} ({state.graph_id}) completed "
                f"in {state.iteration} iterations, {len(state.nodes_visited)} nodes visited"
            ),
            artifacts=state.artifacts,
            sources=state.context_sources,
            execution_trace=self._audit.snapshot(),
            policy_decisions=self._policy.decisions_log(),
        )

    def _walk_nodes(self, nodes: list[dict], edges: Any, state: _GraphState,
                     budget: WorkflowBudgetGuard, max_iter: int) -> None:
        node_map = {n["id"]: n for n in nodes}
        current = nodes[0]["id"] if nodes else "end"

        while state.iteration < max_iter:
            state.iteration += 1
            node = node_map.get(current)
            if not node:
                state.stopped_at = current
                state.stop_reason = "node_not_found"
                break
            state.current_node = current
            state.nodes_visited.append(current)

            kind = node.get("kind", "llm")
            budget.record_step(f"node:{current}")
            self._audit.log("node_enter", task_id=state.task_id, node=current, kind=kind)

            # Human gate — would stop and request approval in live flow
            if kind == "human_gate":
                state.stopped_at = current
                state.stop_reason = "human_gate"
                self._audit.log("human_gate_stop", task_id=state.task_id, node=current)
                break

            if kind == "end" or current == "end":
                state.stopped_at = current
                state.stop_reason = "end_node"
                break

            # Follow first edge from current node
            edge_list = edges if isinstance(edges, list) else []
            next_node = None
            for e in edge_list:
                if e.get("from") == current:
                    next_node = e.get("to")
                    break
            if next_node is None:
                state.stopped_at = current
                state.stop_reason = "no_outbound_edge"
                break
            current = next_node

        else:
            state.stopped_at = current
            state.stop_reason = "max_iterations"

    def _build_plan(self, task_type: str, nodes: list[dict],
                     retriever: str) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        if retriever == "codecompass":
            steps.append({"step": 1, "action": "codecompass_query",
                           "description": "Fetch context from CodeCompass"})
        if nodes:
            for i, n in enumerate(nodes, start=len(steps) + 1):
                steps.append({"step": i, "action": f"node:{n['id']}",
                               "description": f"Execute {n.get('kind','?')} node: {n['id']}"})
        else:
            steps.append({"step": len(steps) + 1, "action": f"langgraph_{task_type}",
                           "description": f"Execute LangGraph {task_type} workflow"})
            steps.append({"step": len(steps) + 1, "action": "artifact_write",
                           "description": "Write result as artifact (artifact_first)"})
        return steps

    def _blocked_result(self, task_id: str, task_type: str,
                          reason_code: str, message: str) -> WorkflowArtifactResult:
        self._audit.log("execute_blocked", task_id=task_id, reason_code=reason_code)
        # Snapshot the execute-path events into the result so the
        # audit log does not leak into the next task.
        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph", task_id=task_id, task_type=task_type,
            status="blocked", summary=message, error=message, reason_code=reason_code,
            execution_trace=self._audit.snapshot(),
        )

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
