"""Streaming projection for the LangGraph worker adapter."""

from __future__ import annotations

from typing import Any

from ananta_contracts.workflow_fallback import (
    RuntimeFallbackRequest,
    workflow_runtime_fallback_policy,
)
from worker.adapters.langgraph_legacy_nodes import LangGraphState
from worker.adapters.workflow_adapter_base import WorkflowArtifactResult
from worker.adapters.workflow_budget import WorkflowBudgetGuard


class LangGraphStreamMixin:
    def stream(self, *, task_id: str, task_type: str, payload: dict[str, Any]):
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

        if payload.get("execution_plan") is not None:
            result = self.execute(task_id=task_id, task_type=task_type, payload=payload)
            for event in result.execution_trace:
                if str(event.get("event") or "").startswith("workflow."):
                    yield {
                        "adapter_id": "adapter.langgraph",
                        "task_id": task_id,
                        "event_type": "execution_plan_event",
                        "payload": dict(event),
                    }
            yield {
                "adapter_id": "adapter.langgraph",
                "task_id": task_id,
                "event_type": "stream_end",
                "result": result.as_dict(),
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
                from langgraph.graph import END, StateGraph  # type: ignore[import]

                descriptor = payload.get("graph_descriptor") or {}
                nodes = descriptor.get("nodes") or []
                edges = descriptor.get("edges") or []
                state_obj = LangGraphState(
                    graph_id=str(payload.get("graph_id") or f"graph-{task_type}"),
                    task_id=task_id,
                )

                def _make_stream_node_fn(node: dict) -> Any:
                    def _fn(lg_state: dict) -> dict:
                        kind = node.get("kind", "llm")
                        budget.record_step(f"node:{node.get('id', '?')}")
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
                checkpointer = self._get_checkpointer(task_id=task_id, payload=payload)
                compiled = graph_builder.compile(checkpointer=checkpointer)

                stream_config = {
                    "recursion_limit": self._config.max_iterations,
                    "configurable": {
                        "thread_id": self._checkpoint_thread_id(task_id, payload),
                        "checkpoint_ns": "",
                    },
                }
                for chunk in compiled.stream({}, config=stream_config):
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
                decision = workflow_runtime_fallback_policy.evaluate(
                    RuntimeFallbackRequest.create(
                        source_runtime="langgraph.compiled_stream",
                        target_runtime="langgraph.batch",
                        reason_code="compiled_graph_stream_failed",
                        semantic_class="degraded",
                        source_capabilities={"policy", "audit", "graph_execution", "streaming"},
                        target_capabilities={"policy", "audit", "graph_execution"},
                        explicitly_enabled=bool(
                            (self._config.metadata or {}).get("allow_manual_walker_fallback", False)
                        ),
                    )
                )
                self._audit.log(
                    "stream_compiled_graph_fallback_evaluated",
                    task_id=task_id,
                    exception_type=type(exc).__name__,
                    **decision.to_dict(),
                )
                result = WorkflowArtifactResult(
                    adapter_id="adapter.langgraph",
                    task_id=task_id,
                    task_type=task_type,
                    status="failed",
                    summary="Compiled LangGraph stream failed; batch fallback is not semantics-preserving.",
                    error="compiled graph stream unavailable",
                    reason_code="compiled_graph_stream_fallback_blocked",
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

        # Batch fallback: execute() then yield single stream_end
        result = self.execute(task_id=task_id, task_type=task_type, payload=payload)
        yield {
            "adapter_id": "adapter.langgraph",
            "task_id": task_id,
            "event_type": "stream_end",
            "result": result.as_dict(),
        }



__all__ = ["LangGraphStreamMixin"]

