"""UTCR-005: Single entry-point for ananta-tool execution.

``UnifiedToolExecutionService.execute()`` enforces the policy gate before
dispatching to ``execute_ananta_tool`` so that callers outside the
tool-loop (e.g. native OpenAI tool calls routed through SGPT) get the
same policy enforcement as the worker loop.
"""
from __future__ import annotations

from typing import Any

from agent.services.tools._evidence import build_tool_result


class UnifiedToolExecutionService:
    """Single execution gateway: policy gate → execute_ananta_tool."""

    def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        mutation_mode: str = "read_only",
        task_id: str | None = None,
        goal_id: str | None = None,
        workspace_dir: str = ".",
        tool_call_id: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate the policy gate and, if allowed, execute the tool.

        Returns a ``ananta_tool_result.v1`` dict with ``policy_decision``
        attached. If the policy blocks the call the result carries
        ``status=decision.decision`` and no execution is attempted.
        """
        from agent.services.ananta_tool_policy_service import get_ananta_tool_policy_service
        from agent.services.tools import execute_ananta_tool

        name = str(tool_name or "").strip()
        args = dict(arguments or {})
        call_id = str(tool_call_id or "")

        policy = get_ananta_tool_policy_service()
        decision = policy.evaluate(
            tool_name=name,
            arguments=args,
            allowed_tools=allowed_tools,
            mutation_mode=mutation_mode,
            task_id=task_id,
            goal_id=goal_id,
        )

        if not decision.allowed:
            return build_tool_result(
                tool_name=name,
                tool_call_id=call_id,
                status=decision.decision,
                risk_class=decision.risk_class,
                error=decision.reason,
                policy_decision=decision.as_dict(),
            )

        result = execute_ananta_tool(
            tool_name=name,
            arguments=args,
            workspace_dir=str(workspace_dir or "."),
            tool_call_id=call_id,
            config=dict(config or {}),
        )
        result["policy_decision"] = decision.as_dict()
        return result


_unified_tool_execution_service = UnifiedToolExecutionService()


def get_unified_tool_execution_service() -> UnifiedToolExecutionService:
    return _unified_tool_execution_service
