"""LangGraph Worker Adapter (LCG-008, LCG-010, LCG-011, LCG-012, LCG-017, LCG-018, LCG-019, LCG-020, LCG-031..LCG-051).

Optional dependency: langgraph is NOT imported at module load time.
Stateful graph workflows; human gates enforce approval before write/delete/network nodes.
CodeCompass is the only allowed retriever source.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.providers.lc_lg import LangGraphProviderConfig
from worker.adapters.chain_runners import (
    LangChainRunnableRunner,
    SimplexRunner,
)
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

_MAX_SUBGRAPH_DEPTH = 3


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
    llm_responses: list[dict[str, Any]] = field(default_factory=list)


class LangGraphAdapter:
    """Optional LangGraph worker adapter."""

    def __init__(self, config: LangGraphProviderConfig | None = None) -> None:
        self._config = config or LangGraphProviderConfig.default_off()
        self._policy = WorkflowPolicyGate(
            external_calls_allowed=self._config.external_calls_allowed,
            allowed_tools=set(),
            human_required_actions=set(self._config.human_in_loop_required_for),
        )
        self._audit = WorkflowAuditLog(adapter_id="adapter.langgraph")
        # LCG-032: use embedding_provider_scope from config instead of hardcode
        self._retriever = CodeCompassRetriever(
            scope=self._config.embedding_provider_scope,
        )

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
                "dry_run", "agent_workflow", "multi_step_plan",
                "human_in_loop", "stateful_task", "review_workflow",
                "checkpointing", "codecompass_retriever",
            ],
            version="1.0",
            provider_diagnostics=provider_diagnostics,
        )

    # ── Dry-run ────────────────────────────────────────────────────────────────

    def dry_run(self, *, task_id: str, task_type: str,
                 payload: dict[str, Any]) -> DryRunResult:
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
            n["id"] for n in nodes
            if n.get("kind") == "tool" and self._policy.requires_human(n.get("tool_ref", ""))
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
                result.approval_reasons = (
                    [f"human_gate_node:{n}" for n in human_gate_nodes]
                    + [f"high_risk_tool_node:{n}" for n in high_risk_nodes]
                )

        self._audit.log("dry_run_complete", task_id=task_id,
                         blocked=result.blocked, approval_required=result.approval_required)
        result.metadata["dry_run_audit_trace"] = self._audit.snapshot()
        return result

    # ── Live execute ───────────────────────────────────────────────────────────

    def execute(self, *, task_id: str, task_type: str,
                 payload: dict[str, Any],
                 resume_token: str | None = None) -> WorkflowArtifactResult:
        self._audit.snapshot()
        self._audit.log("execute_start", task_id=task_id, task_type=task_type)

        if not self._config.is_live():
            return self._blocked_result(
                task_id, task_type,
                "live_execution_requires_live_mode",
                "Adapter is in dry_run mode; set mode=local_live to execute.",
            )

        # LCG-049: resume path bypasses dry_run gate
        if resume_token is not None:
            return self._resume_from_token(task_id, task_type, payload, resume_token)

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
                execution_trace=self._audit.snapshot(),
                policy_decisions=self._policy.decisions_log(),
            )

        self._audit.log("execute_complete", task_id=task_id, status=result.status)
        return result

    # ── Internal ───────────────────────────────────────────────────────────────

    def _run_graph(self, task_id: str, task_type: str,
                    payload: dict[str, Any], budget: WorkflowBudgetGuard,
                    _depth: int = 0) -> WorkflowArtifactResult:
        state = _GraphState(
            graph_id=str(payload.get("graph_id") or f"graph-{task_type}"),
            task_id=task_id,
        )

        retriever = payload.get("retriever_ref") or "none"
        if retriever == "codecompass":
            query = str(payload.get("query") or payload.get("prompt") or "")
            if query:
                ctx = self._retriever.query(query, max_results=5)
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

        # LCG-047: try compiled graph path first when langgraph is available
        if nodes and self._langgraph_available() and _depth == 0:
            try:
                return self._run_compiled_graph(task_id, task_type, payload, budget)
            except Exception as exc:  # noqa: BLE001
                self._audit.log("compiled_graph_failed_fallback",
                                task_id=task_id, reason=str(exc)[:200])

        if nodes:
            self._walk_nodes(nodes, edges, state, budget, max_iter,
                              payload=payload, _depth=_depth)
        else:
            state.nodes_visited = ["llm", "artifact_writer", "end"]
            state.stopped_at = "end"
            state.stop_reason = "end_node"
            budget.record_step("llm_call")
            budget.record_step("artifact_write")

        # LCG-049: produce resume_token if stopped at human_gate
        resume_token: str | None = None
        if state.stop_reason == "human_gate":
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

    def _walk_nodes(self, nodes: list[dict], edges: Any, state: _GraphState,
                     budget: WorkflowBudgetGuard, max_iter: int,
                     payload: dict[str, Any] | None = None,
                     _depth: int = 0) -> None:
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

            if kind == "human_gate":
                state.stopped_at = current
                state.stop_reason = "human_gate"
                self._audit.log("human_gate_stop", task_id=state.task_id, node=current)
                break

            if kind == "end" or current == "end":
                state.stopped_at = current
                state.stop_reason = "end_node"
                break

            if kind == "llm":
                try:
                    self._invoke_llm_node(node=node, state=state, budget=budget)
                except WorkerError as exc:
                    state.stopped_at = current
                    state.stop_reason = exc.reason_code
                    self._audit.log("node_failed", task_id=state.task_id,
                                    node=current, reason_code=exc.reason_code)
                    raise

            elif kind == "tool":
                # LCG-042
                self._invoke_tool_node(node=node, state=state, budget=budget)

            elif kind == "retriever":
                # LCG-043
                self._invoke_retriever_node(node=node, state=state, budget=budget)

            elif kind == "artifact_writer":
                # LCG-044
                self._invoke_artifact_writer_node(node=node, state=state, budget=budget)

            elif kind == "router":
                # LCG-045: router with conditional edges
                next_node = self._route(node, state, edges)
                if next_node is None:
                    state.stopped_at = current
                    state.stop_reason = "no_matching_route"
                    break
                current = next_node
                continue

            elif kind == "subgraph":
                # LCG-051
                if payload is not None:
                    self._invoke_subgraph_node(
                        node=node, state=state, budget=budget,
                        parent_payload=payload, _depth=_depth,
                    )

            # Follow first matching edge (non-router nodes)
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

    # ── Node implementations ───────────────────────────────────────────────────

    def _invoke_llm_node(self, *, node: dict[str, Any], state: _GraphState,
                          budget: WorkflowBudgetGuard) -> None:
        prompt = self._build_node_prompt(node, state)
        runner_label = "simplex"
        runner_obj: Any = SimplexRunner()
        if self._langgraph_available():
            runner_label = "langchain_runnable"
            runner_obj = LangChainRunnableRunner()
        # LCG-031: use model_provider_ref from config directly
        model_ref = self._config.model_provider_ref
        response = runner_obj.run(
            prompt=prompt,
            payload={"node": node.get("id", ""), "graph_id": state.graph_id},
            budget=budget,
            model_provider_ref=model_ref,
        )
        state.llm_responses.append({
            "node_id": node.get("id", ""),
            "runner": runner_label,
            "response": str(response)[:1000],
        })
        self._audit.log("node_llm_invoked", task_id=state.task_id,
                         node=node.get("id", ""), runner=runner_label)

    def _invoke_tool_node(self, *, node: dict[str, Any], state: _GraphState,
                           budget: WorkflowBudgetGuard) -> None:
        # LCG-042
        tool_ref = node.get("tool_ref", "")
        if not tool_ref:
            raise WorkerError(
                "tool_ref_missing",
                f"tool node '{node.get('id', '?')}' has no tool_ref",
            )
        decision = self._policy.check_tool(tool_ref)
        if not decision["allowed"]:
            self._audit.log("tool_node_blocked", task_id=state.task_id,
                             node=node.get("id", ""), tool_ref=tool_ref,
                             reason=decision["reason"])
            raise WorkerError(
                "tool_blocked_by_policy",
                f"Tool '{tool_ref}' blocked: {decision['reason']}",
            )
        budget.record_step(f"tool:{tool_ref}")
        self._audit.log("tool_node_invoked", task_id=state.task_id,
                         node=node.get("id", ""), tool_ref=tool_ref)
        state.llm_responses.append({
            "node_id": node.get("id", ""),
            "runner": "tool_node",
            "tool_ref": tool_ref,
            "status": "invoked",
            "result": "tool_execution_not_wired",
        })

    def _invoke_retriever_node(self, *, node: dict[str, Any], state: _GraphState,
                                budget: WorkflowBudgetGuard) -> None:
        # LCG-043
        retriever_ref = node.get("retriever_ref", "codecompass")
        if retriever_ref and retriever_ref != "codecompass":
            raise WorkerError(
                "retriever_ref_not_allowed",
                f"Only codecompass retriever allowed, got: {retriever_ref}",
            )
        query = ""
        if state.llm_responses:
            query = str(state.llm_responses[-1].get("response", ""))[:500]
        if not query:
            query = state.graph_id
        budget.record_step(f"retriever:{node.get('id', '?')}")
        ctx = self._retriever.query(query, max_results=5)
        new_sources = ctx.get("sources", [])
        state.context_sources.extend(new_sources)
        self._audit.log("retriever_node_invoked", task_id=state.task_id,
                         node=node.get("id", ""), sources_added=len(new_sources))

    def _invoke_artifact_writer_node(self, *, node: dict[str, Any], state: _GraphState,
                                      budget: WorkflowBudgetGuard) -> None:
        # LCG-044
        artifact_id = f"artifact-lg-node-{uuid.uuid4().hex[:8]}"
        artifact_type = node.get("artifact_type", "report")
        content = ""
        if state.llm_responses:
            content = str(state.llm_responses[-1].get("response", ""))
        budget.record_step(f"artifact_writer:{node.get('id', '?')}")
        self._audit.log("artifact_writer_node_invoked", task_id=state.task_id,
                         node=node.get("id", ""), artifact_id=artifact_id)
        state.artifacts.append({
            "artifact_id": artifact_id,
            "node_id": node.get("id", ""),
            "artifact_type": artifact_type,
            "content": content,
            "sources": list(state.context_sources),
            "status": "created",
        })

    def _route(self, node: dict[str, Any], state: _GraphState,
                edges: Any) -> str | None:
        # LCG-045: conditional edge routing
        edge_list = edges if isinstance(edges, list) else []
        current = node.get("id", "")
        self._audit.log("router_node_entry", task_id=state.task_id, node=current)

        # First pass: try conditional edges
        for e in edge_list:
            if e.get("from") != current:
                continue
            condition = e.get("condition")
            if not condition:
                # No condition on this edge — skip in first pass, use in second
                continue
            if isinstance(condition, dict):
                on_stop_reason = condition.get("on_stop_reason")
                on_state_key = condition.get("on_state_key")
                on_state_value = condition.get("on_state_value")
                if on_stop_reason and state.stop_reason == on_stop_reason:
                    self._audit.log("router_node_routed", task_id=state.task_id,
                                     node=current, destination=e.get("to"),
                                     via="on_stop_reason")
                    return e.get("to")
                if on_state_key and on_state_value:
                    actual = None
                    if state.llm_responses:
                        actual = state.llm_responses[-1].get(on_state_key)
                    if str(actual) == str(on_state_value):
                        self._audit.log("router_node_routed", task_id=state.task_id,
                                         node=current, destination=e.get("to"),
                                         via="on_state_value")
                        return e.get("to")
            elif isinstance(condition, str):
                # String condition treated as literal on_stop_reason for backwards compat
                if state.stop_reason == condition:
                    self._audit.log("router_node_routed", task_id=state.task_id,
                                     node=current, destination=e.get("to"),
                                     via="condition_string")
                    return e.get("to")

        # Second pass: fallback to first unconditional edge
        for e in edge_list:
            if e.get("from") == current:
                self._audit.log("router_node_routed", task_id=state.task_id,
                                 node=current, destination=e.get("to"),
                                 via="unconditional_fallback")
                return e.get("to")

        self._audit.log("router_no_matching_route", task_id=state.task_id, node=current)
        return None

    def _invoke_subgraph_node(self, *, node: dict[str, Any], state: _GraphState,
                               budget: WorkflowBudgetGuard,
                               parent_payload: dict[str, Any],
                               _depth: int) -> None:
        # LCG-051
        if _depth >= _MAX_SUBGRAPH_DEPTH:
            raise WorkerError(
                "subgraph_depth_exceeded",
                f"Maximum subgraph depth {_MAX_SUBGRAPH_DEPTH} exceeded",
            )
        subgraph_ref = node.get("subgraph_ref", "")
        parent_graph_id = state.graph_id
        if subgraph_ref == parent_graph_id:
            raise WorkerError(
                "subgraph_cycle_detected",
                f"Subgraph '{subgraph_ref}' references its own parent graph",
            )
        subgraph_descriptors = parent_payload.get("subgraph_descriptors") or {}
        sub_descriptor = subgraph_descriptors.get(subgraph_ref)
        if not sub_descriptor:
            raise WorkerError(
                "subgraph_descriptor_missing",
                f"No subgraph_descriptor found for ref '{subgraph_ref}'",
            )
        sub_payload = dict(parent_payload)
        sub_payload["graph_descriptor"] = sub_descriptor
        sub_payload["graph_id"] = subgraph_ref
        self._audit.log("subgraph_enter", task_id=state.task_id,
                         subgraph_ref=subgraph_ref, depth=_depth + 1)
        budget.record_step(f"subgraph:{subgraph_ref}")

        # Check max_nodes for subgraph topology too (against same limit)
        sub_nodes = sub_descriptor.get("nodes") or []
        if len(sub_nodes) > self._config.max_nodes:
            raise WorkerError(
                "graph_too_many_nodes",
                f"Subgraph '{subgraph_ref}' has {len(sub_nodes)} nodes, "
                f"max_nodes={self._config.max_nodes}",
            )

        sub_edges = sub_descriptor.get("edges") or []
        sub_stop = sub_descriptor.get("stop_conditions") or {}
        sub_max_iter = min(
            sub_stop.get("max_iterations", self._config.max_iterations),
            self._config.max_iterations,
        )
        sub_state = _GraphState(
            graph_id=subgraph_ref,
            task_id=state.task_id,
            context_sources=list(state.context_sources),
        )
        self._walk_nodes(
            sub_nodes, sub_edges, sub_state, budget, sub_max_iter,
            payload=sub_payload, _depth=_depth + 1,
        )
        # Merge subgraph results back into parent state
        state.context_sources = sub_state.context_sources
        state.artifacts.extend(sub_state.artifacts)
        state.llm_responses.extend(sub_state.llm_responses)
        self._audit.log("subgraph_exit", task_id=state.task_id,
                         subgraph_ref=subgraph_ref, stop_reason=sub_state.stop_reason)

    def _invoke_llm_node_from_state(self, *, node_id: str, state: _GraphState,
                                     budget: WorkflowBudgetGuard) -> None:
        fake_node = {"id": node_id, "kind": "llm"}
        self._invoke_llm_node(node=fake_node, state=state, budget=budget)

    def _build_node_prompt(self, node: dict[str, Any], state: _GraphState) -> str:
        try:
            from agent.common.redaction import redact
            _redact = redact
        except ImportError:
            _redact = str
        context_block = "\n\n".join(
            f"[{i+1}] {s.get('path','')}: {s.get('content','')[:300]}"
            for i, s in enumerate(state.context_sources or [])
        )
        prior = ""
        if state.llm_responses:
            prior = "\n\n".join(
                f"[from {r['node_id']}]: {_redact(str(r.get('response', ''))[:300])}"
                for r in state.llm_responses[-2:]
                if "response" in r
            )
        sections = [
            f"Node: {node.get('id','?')} (kind=llm)",
            f"Graph: {state.graph_id}",
        ]
        if context_block:
            sections.append("Context (CodeCompass):\n" + context_block)
        if prior:
            sections.append("Prior node responses:\n" + prior)
        return "\n\n".join(sections)

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
        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph", task_id=task_id, task_type=task_type,
            status="blocked", summary=message, error=message, reason_code=reason_code,
            execution_trace=self._audit.snapshot(),
        )

    def _serialize_state(self, state: _GraphState) -> str:
        # LCG-049: serialize state for resume_token; apply redaction to llm_responses
        try:
            from agent.common.redaction import redact
            _redact = redact
        except ImportError:
            _redact = str
        safe_responses = [
            {k: _redact(str(v)) if isinstance(v, str) else v for k, v in r.items()}
            for r in state.llm_responses
        ]
        token_data = {
            "graph_id": state.graph_id,
            "task_id": state.task_id,
            "stopped_at": state.stopped_at,
            "stop_reason": state.stop_reason,
            "nodes_visited": state.nodes_visited,
            "iteration": state.iteration,
            "llm_responses": safe_responses,
        }
        return json.dumps(token_data)

    def _resume_from_token(self, task_id: str, task_type: str,
                            payload: dict[str, Any],
                            resume_token: str) -> WorkflowArtifactResult:
        # LCG-049: resume execution from serialized state
        try:
            token_data = json.loads(resume_token)
        except (ValueError, TypeError):
            self._audit.log("resume_token_invalid", task_id=task_id)
            return WorkflowArtifactResult(
                adapter_id="adapter.langgraph", task_id=task_id, task_type=task_type,
                status="failed", summary="Invalid resume_token",
                error="resume_token could not be parsed",
                reason_code="resume_token_invalid",
                execution_trace=self._audit.snapshot(),
            )

        self._audit.log("resume_start", task_id=task_id,
                         stopped_at=token_data.get("stopped_at"))

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
                adapter_id="adapter.langgraph", task_id=task_id, task_type=task_type,
                status="failed", summary="Cannot resume: no outbound edge from stopped_at node",
                error=f"No outbound edge from {stopped_at}",
                reason_code="resume_no_outbound_edge",
                execution_trace=self._audit.snapshot(),
            )

        state = _GraphState(
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
        self._walk_nodes(resume_nodes, edges, state, budget, max_iter,
                          payload=payload, _depth=0)

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

    def _run_compiled_graph(self, task_id: str, task_type: str,
                             payload: dict[str, Any],
                             budget: WorkflowBudgetGuard) -> WorkflowArtifactResult:
        """Optional live path using StateGraph.compile() when langgraph is installed.

        Builds a real LangGraph StateGraph from graph_descriptor, compiles it
        with an optional checkpointer, and invokes it. Falls back to _walk_nodes
        on any error.
        """
        from langgraph.graph import StateGraph, END  # type: ignore[import]

        descriptor = payload.get("graph_descriptor") or {}
        nodes = descriptor.get("nodes") or []
        edges = descriptor.get("edges") or []
        state_obj = _GraphState(
            graph_id=str(payload.get("graph_id") or f"graph-{task_type}"),
            task_id=task_id,
        )

        # Build adapter node functions that call into our existing node implementations
        def _make_node_fn(node: dict) -> Any:
            def _node_fn(lg_state: dict) -> dict:
                budget.record_step(f"node:{node.get('id', '?')}")
                kind = node.get("kind", "llm")
                self._audit.log("node_enter", task_id=task_id,
                                node=node.get("id", ""), kind=kind)
                if kind == "llm":
                    self._invoke_llm_node(node=node, state=state_obj, budget=budget)
                elif kind == "tool":
                    self._invoke_tool_node(node=node, state=state_obj, budget=budget)
                elif kind == "retriever":
                    self._invoke_retriever_node(node=node, state=state_obj, budget=budget)
                elif kind == "artifact_writer":
                    self._invoke_artifact_writer_node(node=node, state=state_obj, budget=budget)
                elif kind == "human_gate":
                    state_obj.stopped_at = node.get("id", "")
                    state_obj.stop_reason = "human_gate"
                    self._audit.log("human_gate_stop", task_id=task_id,
                                    node=node.get("id", ""))
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

        checkpointer = self._get_checkpointer()
        compiled = graph_builder.compile(checkpointer=checkpointer)

        compiled.invoke({}, config={"recursion_limit": self._config.max_iterations})

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
        if state_obj.stop_reason == "human_gate":
            resume_token = self._serialize_state(state_obj)

        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph",
            task_id=task_id,
            task_type=task_type,
            status=final_status,
            summary=(
                f"LangGraph compiled {task_type} ({state_obj.graph_id}) completed "
                f"in {state_obj.iteration} iterations"
            ),
            artifacts=state_obj.artifacts,
            sources=state_obj.context_sources,
            execution_trace=self._audit.snapshot(),
            policy_decisions=self._policy.decisions_log(),
            resume_token=resume_token,
            reason_code="human_gate" if state_obj.stop_reason == "human_gate" else "",
        )

    # ── LCG-048: Checkpointing ─────────────────────────────────────────────────

    def _get_checkpointer(self) -> Any:
        """Return a LangGraph checkpointer based on checkpoint_policy config.

        Returns None when:
        - checkpoint_policy is 'none'
        - checkpoint_policy is 'hub_owned' (not yet wired)
        - langgraph is not installed
        """
        policy = self._config.checkpoint_policy
        if policy == "none":
            return None
        if policy == "hub_owned":
            return None
        if policy in ("local_ephemeral", "local_ephemeral_or_hub_owned"):
            try:
                from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]
                return MemorySaver()
            except ImportError:
                return None
        return None

    # ── LCG-050: stream() ──────────────────────────────────────────────────────

    def stream(self, *, task_id: str, task_type: str,
               payload: dict[str, Any]):
        """Yield stream events for a graph execution.

        Policy gate (dry_run) is checked before the generator body starts.
        Each event is a dict with adapter_id, task_id, event_type, and payload.
        The final event has event_type='stream_end' and contains the full result.

        When the compiled graph is not available, yields a single batch event.
        """
        dry = self.dry_run(task_id=task_id, task_type=task_type, payload=payload)
        if dry.blocked:
            yield {
                "adapter_id": "adapter.langgraph",
                "task_id": task_id,
                "event_type": "stream_blocked",
                "reason": dry.block_reason,
            }
            return

        budget = WorkflowBudgetGuard(
            max_steps=self._config.max_iterations,
            timeout_seconds=self._config.timeout_seconds,
            max_tokens=self._config.max_tokens,
        )

        if self._langgraph_available() and self._config.is_live():
            # Attempt compiled graph with event streaming
            try:
                from langgraph.graph import StateGraph, END  # type: ignore[import]

                descriptor = payload.get("graph_descriptor") or {}
                nodes = descriptor.get("nodes") or []
                edges = descriptor.get("edges") or []
                state_obj = _GraphState(
                    graph_id=str(payload.get("graph_id") or f"graph-{task_type}"),
                    task_id=task_id,
                )

                def _make_stream_node_fn(node: dict) -> Any:
                    def _fn(lg_state: dict) -> dict:
                        kind = node.get("kind", "llm")
                        budget.record_step(f"node:{node.get('id', '?')}")
                        if kind == "llm":
                            self._invoke_llm_node(node=node, state=state_obj, budget=budget)
                        elif kind == "tool":
                            self._invoke_tool_node(node=node, state=state_obj, budget=budget)
                        state_obj.nodes_visited.append(node.get("id", ""))
                        state_obj.iteration += 1
                        return lg_state
                    return _fn

                graph_builder: Any = StateGraph(dict)
                node_ids = set()
                for node in nodes:
                    nid = node.get("id", "")
                    if not nid or node.get("kind") == "end":
                        continue
                    graph_builder.add_node(nid, _make_stream_node_fn(node))
                    node_ids.add(nid)
                if nodes:
                    first_id = nodes[0].get("id", "")
                    if first_id and first_id in node_ids:
                        graph_builder.set_entry_point(first_id)
                for edge in edges:
                    frm, to = edge.get("from", ""), edge.get("to", "")
                    if frm in node_ids:
                        graph_builder.add_edge(frm, END if to not in node_ids else to)
                compiled = graph_builder.compile()

                for chunk in compiled.stream({}, config={"recursion_limit": self._config.max_iterations}):
                    for node_name, node_output in chunk.items():
                        yield {
                            "adapter_id": "adapter.langgraph",
                            "task_id": task_id,
                            "event_type": "node_complete",
                            "node_id": node_name,
                            "output": node_output,
                        }

                result = WorkflowArtifactResult(
                    adapter_id="adapter.langgraph",
                    task_id=task_id,
                    task_type=task_type,
                    status="success",
                    summary=f"LangGraph stream {task_type} completed",
                    artifacts=state_obj.artifacts,
                    sources=state_obj.context_sources,
                    execution_trace=self._audit.snapshot(),
                    policy_decisions=self._policy.decisions_log(),
                )
                yield {
                    "adapter_id": "adapter.langgraph",
                    "task_id": task_id,
                    "event_type": "stream_end",
                    "result": result.as_dict(),
                }
                return
            except Exception as exc:  # noqa: BLE001
                self._audit.log("stream_compiled_graph_failed", task_id=task_id,
                                reason=str(exc)[:200])

        # Batch fallback: execute() then yield single stream_end
        result = self.execute(task_id=task_id, task_type=task_type, payload=payload)
        yield {
            "adapter_id": "adapter.langgraph",
            "task_id": task_id,
            "event_type": "stream_end",
            "result": result.as_dict(),
        }

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
