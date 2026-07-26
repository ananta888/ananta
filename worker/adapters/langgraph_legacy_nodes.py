"""Legacy LangGraph node execution isolated from adapter lifecycle wiring."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from worker.adapters.chain_runners import (
    LangChainRunnableRunner,
    SimplexRunner,
    validate_chain_output,
)
from worker.adapters.workflow_adapter_base import WorkerError
from worker.adapters.workflow_budget import WorkflowBudgetGuard
from worker.core.tool_calling_pipeline import ToolCallRequest
from worker.retrieval.codecompass_retriever import retrieval_request_from_payload

MAX_SUBGRAPH_DEPTH = 3


@dataclass
class LangGraphState:
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


class LangGraphLegacyNodeMixin:
    def _walk_nodes(
        self,
        nodes: list[dict],
        edges: Any,
        state: LangGraphState,
        budget: WorkflowBudgetGuard,
        max_iter: int,
        payload: dict[str, Any] | None = None,
        _depth: int = 0,
    ) -> None:
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
                    self._invoke_llm_node(
                        node=node,
                        state=state,
                        budget=budget,
                        execution_payload=payload or {},
                    )
                except WorkerError as exc:
                    state.stopped_at = current
                    state.stop_reason = exc.reason_code
                    self._audit.log("node_failed", task_id=state.task_id, node=current, reason_code=exc.reason_code)
                    raise

            elif kind == "tool":
                # LCG-042
                self._invoke_tool_node(
                    node=node,
                    state=state,
                    budget=budget,
                    payload=payload or {},
                )

            elif kind == "retriever":
                # LCG-043
                self._invoke_retriever_node(
                    node=node,
                    state=state,
                    budget=budget,
                    payload=payload or {},
                )

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
                        node=node,
                        state=state,
                        budget=budget,
                        parent_payload=payload,
                        _depth=_depth,
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

    def _invoke_llm_node(
        self,
        *,
        node: dict[str, Any],
        state: LangGraphState,
        budget: WorkflowBudgetGuard,
        execution_payload: dict[str, Any] | None = None,
    ) -> None:
        prompt = self._build_node_prompt(node, state)
        runner_label = "simplex"
        runner_obj: Any = SimplexRunner()
        if self._langgraph_available():
            runner_label = "langchain_runnable"
            runner_obj = LangChainRunnableRunner()
        # LCG-031: use model_provider_ref from config directly
        model_ref = self._config.model_provider_ref
        provider_context = self._bound_provider_context(execution_payload or {})
        provider_contexts = self._bound_provider_contexts_by_profile_id(
            execution_payload or {},
            primary=provider_context,
        )
        response = runner_obj.run(
            prompt=prompt,
            payload={
                "node": node.get("id", ""),
                "graph_id": state.graph_id,
                "provider_context": provider_context,
                "provider_contexts_by_profile_id": provider_contexts,
                "model_routing": (execution_payload or {}).get(
                    "model_routing"
                ),
            },
            budget=budget,
            model_provider_ref=model_ref,
        )
        validation_payload = dict(execution_payload or {})
        node_contract = dict(node.get("metadata") or {})
        for key in ("output_schema", "output_format"):
            if key in node:
                node_contract[key] = node[key]
        validated_output = validate_chain_output(
            response,
            payload=validation_payload,
            node=node_contract,
        )
        state.llm_responses.append(
            {
                "node_id": node.get("id", ""),
                "runner": runner_label,
                "response": validated_output.value,
                "structured_output_validated": validated_output.structured,
            }
        )
        self._audit.log("node_llm_invoked", task_id=state.task_id, node=node.get("id", ""), runner=runner_label)

    @staticmethod
    def _bound_provider_context(payload: dict[str, Any]) -> dict[str, Any] | None:
        raw = payload.get("provider_context")
        delegated = any(payload.get(name) for name in ("tenant_id", "run_id", "workflow_id", "plan_hash"))
        if raw is None:
            if delegated:
                raise WorkerError(
                    "provider_context_binding_missing",
                    "Delegated LangGraph LLM nodes require a Hub-bound provider context.",
                )
            return None
        if not isinstance(raw, dict):
            raise WorkerError(
                "provider_context_invalid",
                "provider_context must be an object.",
            )
        context = dict(raw)
        for name in ("tenant_id", "run_id", "workflow_id", "policy_version", "plan_hash"):
            expected = str(payload.get(name) or "")
            if expected and str(context.get(name) or "") != expected:
                raise WorkerError(
                    f"provider_context_{name}_mismatch",
                    "Provider context does not match the delegated execution binding.",
                )
        return context

    @staticmethod
    def _bound_provider_contexts_by_profile_id(
        payload: dict[str, Any],
        *,
        primary: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]] | None:
        raw = payload.get("provider_contexts_by_profile_id")
        if raw is None:
            return None
        if not isinstance(raw, dict) or len(raw) > 8 or primary is None:
            raise WorkerError(
                "provider_profile_contexts_invalid",
                "Hub profile contexts must be a bounded object.",
            )
        contexts: dict[str, dict[str, Any]] = {}
        scope_fields = (
            "tenant_id",
            "workflow_id",
            "run_id",
            "step_id",
            "plan_hash",
            "policy_version",
            "prompt_version",
            "attempt_id",
            "fencing_token",
            "authorization_envelope",
        )
        for raw_profile_id, raw_context in raw.items():
            profile_id = str(raw_profile_id or "").strip()
            if (
                not profile_id
                or len(profile_id) > 256
                or "\x00" in profile_id
                or not isinstance(raw_context, dict)
            ):
                raise WorkerError(
                    "provider_profile_contexts_invalid",
                    "Hub profile contexts contain an invalid binding.",
                )
            context = dict(raw_context)
            if any(
                context.get(field) != primary.get(field)
                for field in scope_fields
            ):
                raise WorkerError(
                    "provider_profile_context_scope_mismatch",
                    "Hub profile context does not match the primary delegation.",
                )
            contexts[profile_id] = context
        return contexts

    def _invoke_tool_node(
        self,
        *,
        node: dict[str, Any],
        state: LangGraphState,
        budget: WorkflowBudgetGuard,
        payload: dict[str, Any],
    ) -> None:
        # LCG-042
        tool_ref = node.get("tool_ref", "")
        if not tool_ref:
            raise WorkerError(
                "tool_ref_missing",
                f"tool node '{node.get('id', '?')}' has no tool_ref",
            )
        decision = self._policy.check_tool(tool_ref)
        if not decision["allowed"]:
            self._audit.log(
                "tool_node_blocked",
                task_id=state.task_id,
                node=node.get("id", ""),
                tool_ref=tool_ref,
                reason=decision["reason"],
            )
            raise WorkerError(
                "tool_blocked_by_policy",
                f"Tool '{tool_ref}' blocked: {decision['reason']}",
            )
        if self._tool_pipeline is None:
            self._audit.log(
                "tool_node_unavailable",
                task_id=state.task_id,
                node=node.get("id", ""),
                tool_ref=tool_ref,
                reason_code="tool_execution_not_configured",
            )
            raise WorkerError(
                "tool_execution_not_configured",
                "Tool execution requires an explicitly configured, policy-bound pipeline.",
            )

        node_id = str(node.get("id") or "tool")
        argument_sets = payload.get("tool_arguments") or {}
        arguments = node.get("arguments") or node.get("tool_arguments") or {}
        if isinstance(argument_sets, dict) and isinstance(argument_sets.get(node_id), dict):
            arguments = argument_sets[node_id]
        if not isinstance(arguments, dict):
            raise WorkerError(
                "tool_arguments_invalid",
                f"Tool node '{node_id}' arguments must be an object.",
            )

        authorization_envelope = payload.get("authorization_envelope") or {}
        if not isinstance(authorization_envelope, dict):
            raise WorkerError(
                "authorization_envelope_invalid",
                "authorization_envelope must be an object.",
            )

        budget.record_step(f"tool:{tool_ref}")
        outcome = self._tool_pipeline.execute(
            ToolCallRequest(
                tenant_id=str(payload.get("tenant_id") or ""),
                workflow_id=str(payload.get("workflow_id") or ""),
                run_id=str(payload.get("run_id") or state.task_id),
                step_id=str(payload.get("step_id") or node_id),
                plan_hash=str(payload.get("plan_hash") or ""),
                policy_version=str(payload.get("policy_version") or ""),
                correlation_id=str(payload.get("correlation_id") or ""),
                attempt_id=str(payload.get("attempt_id") or f"{state.task_id}:{node_id}"),
                fencing_token=int(payload.get("fencing_token") or 0),
                tool_id=str(tool_ref),
                arguments=dict(arguments),
                authorization_envelope=dict(authorization_envelope),
                allowed_policy_scopes=tuple(
                    str(item)
                    for item in payload.get("allowed_policy_scopes", ())
                    if str(item).strip()
                ),
                secret_refs=tuple(
                    str(item)
                    for item in payload.get("secret_refs", ())
                    if str(item).strip()
                ),
                approval_ref=(str(payload["approval_ref"]) if payload.get("approval_ref") is not None else None),
                hub_task_id=state.task_id,
            )
        )
        if outcome.status != "success" or outcome.result is None:
            self._audit.log(
                "tool_node_failed",
                task_id=state.task_id,
                node=node_id,
                tool_ref=tool_ref,
                reason_code=outcome.reason_code,
                operation_id=outcome.operation_id,
            )
            raise WorkerError(
                outcome.reason_code or "tool_execution_failed",
                f"Tool '{tool_ref}' did not complete successfully.",
            )

        self._audit.log(
            "tool_node_completed",
            task_id=state.task_id,
            node=node_id,
            tool_ref=tool_ref,
            operation_id=outcome.operation_id,
        )
        state.llm_responses.append(
            {
                "node_id": node_id,
                "runner": "tool_node",
                "tool_ref": tool_ref,
                "status": "completed",
                "operation_id": outcome.operation_id,
                "result": dict(outcome.result),
            }
        )

    def _invoke_retriever_node(
        self,
        *,
        node: dict[str, Any],
        state: LangGraphState,
        budget: WorkflowBudgetGuard,
        payload: dict[str, Any],
    ) -> None:
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
        request = retrieval_request_from_payload(
            query=query,
            payload=payload,
            default_scope=self._config.embedding_provider_scope,
            max_results=5,
        )
        ctx = self._retriever.retrieve(request).to_dict()
        new_sources = ctx.get("sources", [])
        state.context_sources.extend(new_sources)
        self._audit.log(
            "retriever_node_invoked", task_id=state.task_id, node=node.get("id", ""), sources_added=len(new_sources)
        )

    def _invoke_artifact_writer_node(
        self, *, node: dict[str, Any], state: LangGraphState, budget: WorkflowBudgetGuard
    ) -> None:
        # LCG-044
        artifact_id = f"artifact-lg-node-{uuid.uuid4().hex[:8]}"
        artifact_type = node.get("artifact_type", "report")
        content = ""
        if state.llm_responses:
            content = str(state.llm_responses[-1].get("response", ""))
        budget.record_step(f"artifact_writer:{node.get('id', '?')}")
        self._audit.log(
            "artifact_writer_node_invoked", task_id=state.task_id, node=node.get("id", ""), artifact_id=artifact_id
        )
        state.artifacts.append(
            {
                "artifact_id": artifact_id,
                "node_id": node.get("id", ""),
                "artifact_type": artifact_type,
                "content": content,
                "sources": list(state.context_sources),
                "status": "created",
            }
        )

    def _route(self, node: dict[str, Any], state: LangGraphState, edges: Any) -> str | None:
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
                    self._audit.log(
                        "router_node_routed",
                        task_id=state.task_id,
                        node=current,
                        destination=e.get("to"),
                        via="on_stop_reason",
                    )
                    return e.get("to")
                if on_state_key and on_state_value:
                    actual = None
                    if state.llm_responses:
                        actual = state.llm_responses[-1].get(on_state_key)
                    if str(actual) == str(on_state_value):
                        self._audit.log(
                            "router_node_routed",
                            task_id=state.task_id,
                            node=current,
                            destination=e.get("to"),
                            via="on_state_value",
                        )
                        return e.get("to")
            elif isinstance(condition, str):
                # String condition treated as literal on_stop_reason for backwards compat
                if state.stop_reason == condition:
                    self._audit.log(
                        "router_node_routed",
                        task_id=state.task_id,
                        node=current,
                        destination=e.get("to"),
                        via="condition_string",
                    )
                    return e.get("to")

        # Second pass: fallback to first unconditional edge
        for e in edge_list:
            if e.get("from") == current:
                self._audit.log(
                    "router_node_routed",
                    task_id=state.task_id,
                    node=current,
                    destination=e.get("to"),
                    via="unconditional_fallback",
                )
                return e.get("to")

        self._audit.log("router_no_matching_route", task_id=state.task_id, node=current)
        return None

    def _invoke_subgraph_node(
        self,
        *,
        node: dict[str, Any],
        state: LangGraphState,
        budget: WorkflowBudgetGuard,
        parent_payload: dict[str, Any],
        _depth: int,
    ) -> None:
        # LCG-051
        if _depth >= MAX_SUBGRAPH_DEPTH:
            raise WorkerError(
                "subgraph_depth_exceeded",
                f"Maximum subgraph depth {MAX_SUBGRAPH_DEPTH} exceeded",
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
        self._audit.log("subgraph_enter", task_id=state.task_id, subgraph_ref=subgraph_ref, depth=_depth + 1)
        budget.record_step(f"subgraph:{subgraph_ref}")

        # Check max_nodes for subgraph topology too (against same limit)
        sub_nodes = sub_descriptor.get("nodes") or []
        if len(sub_nodes) > self._config.max_nodes:
            raise WorkerError(
                "graph_too_many_nodes",
                f"Subgraph '{subgraph_ref}' has {len(sub_nodes)} nodes, max_nodes={self._config.max_nodes}",
            )

        sub_edges = sub_descriptor.get("edges") or []
        sub_stop = sub_descriptor.get("stop_conditions") or {}
        sub_max_iter = min(
            sub_stop.get("max_iterations", self._config.max_iterations),
            self._config.max_iterations,
        )
        sub_state = LangGraphState(
            graph_id=subgraph_ref,
            task_id=state.task_id,
            context_sources=list(state.context_sources),
        )
        self._walk_nodes(
            sub_nodes,
            sub_edges,
            sub_state,
            budget,
            sub_max_iter,
            payload=sub_payload,
            _depth=_depth + 1,
        )
        # Merge subgraph results back into parent state
        state.context_sources = sub_state.context_sources
        state.artifacts.extend(sub_state.artifacts)
        state.llm_responses.extend(sub_state.llm_responses)
        self._audit.log(
            "subgraph_exit", task_id=state.task_id, subgraph_ref=subgraph_ref, stop_reason=sub_state.stop_reason
        )

    def _invoke_llm_node_from_state(self, *, node_id: str, state: LangGraphState, budget: WorkflowBudgetGuard) -> None:
        fake_node = {"id": node_id, "kind": "llm"}
        self._invoke_llm_node(node=fake_node, state=state, budget=budget)

    def _build_node_prompt(self, node: dict[str, Any], state: LangGraphState) -> str:
        try:
            from ananta_contracts.redaction import redact

            _redact = redact
        except ImportError:
            _redact = str
        context_block = "\n\n".join(
            f"[{i + 1}] {s.get('path', '')}: {s.get('content', '')[:300]}"
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
            f"Node: {node.get('id', '?')} (kind=llm)",
            f"Graph: {state.graph_id}",
        ]
        if context_block:
            sections.append("Context (CodeCompass):\n" + context_block)
        if prior:
            sections.append("Prior node responses:\n" + prior)
        return "\n\n".join(sections)

    def _build_plan(self, task_type: str, nodes: list[dict], retriever: str) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        if retriever == "codecompass":
            steps.append({"step": 1, "action": "codecompass_query", "description": "Fetch context from CodeCompass"})
        if nodes:
            for i, n in enumerate(nodes, start=len(steps) + 1):
                steps.append(
                    {
                        "step": i,
                        "action": f"node:{n['id']}",
                        "description": f"Execute {n.get('kind', '?')} node: {n['id']}",
                    }
                )
        else:
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": f"langgraph_{task_type}",
                    "description": f"Execute LangGraph {task_type} workflow",
                }
            )
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "artifact_write",
                    "description": "Write result as artifact (artifact_first)",
                }
            )
        return steps



__all__ = ["LangGraphLegacyNodeMixin", "LangGraphState"]
