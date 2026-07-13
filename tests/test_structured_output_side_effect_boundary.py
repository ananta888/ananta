from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.providers.lc_lg import LangGraphProviderConfig
from worker.adapters.langgraph_adapter import LangGraphAdapter
from worker.core.tool_calling_pipeline import ToolCallDecision, ToolCallingPipeline
from worker.core.tool_registry import ToolResult, build_default_registry


class _AllowAllGates:
    @staticmethod
    def verify(*_args) -> ToolCallDecision:
        return ToolCallDecision(True, "allowed")

    @staticmethod
    def authorize(*_args) -> ToolCallDecision:
        return ToolCallDecision(True, "allowed")

    @staticmethod
    def reserve(*_args) -> ToolCallDecision:
        return ToolCallDecision(True, "allowed")


class _RecordingLedger:
    def __init__(self) -> None:
        self.claims: list[dict] = []
        self.completed: list[dict] = []
        self.failed: list[dict] = []

    def claim(self, **kwargs) -> ToolCallDecision:
        self.claims.append(dict(kwargs))
        return ToolCallDecision(True, "claimed")

    def complete(self, **kwargs) -> None:
        self.completed.append(dict(kwargs))

    def fail(self, **kwargs) -> None:
        self.failed.append(dict(kwargs))


class _WritingInvoker:
    def __init__(self, side_effects: list[str]) -> None:
        self.calls: list[tuple] = []
        self._side_effects = side_effects

    def invoke(self, request, descriptor, *, limits) -> ToolResult:
        self.calls.append((request, descriptor, limits))
        self._side_effects.append("filesystem_write")
        return ToolResult(
            tool_id=request.tool_id,
            execution_id=request.attempt_id,
            success=True,
        )


class _RecordingAudit:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def record(self, event) -> None:
        self.events.append(dict(event))


@pytest.mark.parametrize("runtime_kind", ["manual_walker", "compiled_graph"])
def test_malformed_structured_output_stops_before_writing_tool_pipeline(
    monkeypatch,
    runtime_kind: str,
) -> None:
    side_effects: list[str] = []
    ledger = _RecordingLedger()
    invoker = _WritingInvoker(side_effects)
    audit = _RecordingAudit()
    gates = _AllowAllGates()
    pipeline = ToolCallingPipeline(
        registry=build_default_registry(),
        authorization=gates,
        policy=gates,
        budget=gates,
        approval=gates,
        ledger=ledger,
        invoker=invoker,
        audit=audit,
    )
    config = LangGraphProviderConfig(
        enabled=True,
        mode="local_live",
        checkpoint_policy="none",
        human_in_loop_required_for=[],
        allowed_tools=["apply_patch"],
        max_iterations=5,
        metadata={
            "allow_manual_walker_fallback": True,
            "manual_walker_fallback_mode": "development",
        },
    )
    adapter = LangGraphAdapter(config, tool_pipeline=pipeline)
    if runtime_kind == "compiled_graph":
        pytest.importorskip("langgraph")
    else:
        monkeypatch.setattr(
            "worker.adapters.langgraph_adapter.LangGraphAdapter._langgraph_available",
            staticmethod(lambda: False),
        )
    payload = {
        "graph_descriptor": {
            "nodes": [
                {
                    "id": "draft",
                    "kind": "llm",
                    "output_format": "json",
                    "output_schema": {
                        "type": "object",
                        "required": ["patch_artifact_id"],
                        "properties": {"patch_artifact_id": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
                {
                    "id": "write",
                    "kind": "tool",
                    "tool_ref": "apply_patch",
                    "arguments": {"patch_artifact_id": "patch-1"},
                },
                {"id": "end", "kind": "end"},
            ],
            "edges": [
                {"from": "draft", "to": "write"},
                {"from": "write", "to": "end"},
            ],
        }
    }

    with patch("agent.llm_integration.generate_text", return_value='{"patch_artifact_id":'):
        result = adapter.execute(
            task_id="task-malformed-output",
            task_type="agent_workflow",
            payload=payload,
        )

    assert result.status == "failed"
    assert result.reason_code == "structured_output_validation_failed"
    assert ledger.claims == []
    assert ledger.completed == []
    assert ledger.failed == []
    assert invoker.calls == []
    assert side_effects == []
    assert audit.events == []
    assert not any(
        event.get("event") in {"tool_node_completed", "tool_node_failed"} for event in result.execution_trace
    )
