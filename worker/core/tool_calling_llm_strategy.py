"""ToolCallingLLMStrategy — FA-T009/T021: real API tools= param, no sgpt."""
from __future__ import annotations

from typing import Any

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, ExecutableProposal
from agent.services.model_invocation_service import ModelInvocationService, LLMUnavailableError

_MOCK_ONLY_PROVIDERS = {"mock"}


class ToolCallingLLMStrategy(ProposeStrategy):
    """Strategy using real API tools= parameter on an OpenAI-compatible endpoint."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        from agent.config import settings
        provider = (settings.default_provider or "lmstudio").strip().lower()

        if provider in _MOCK_ONLY_PROVIDERS:
            return ProposeStrategyResult.declined(
                "tool_calling_llm",
                reason="provider_tools_not_supported_mock",
            )

        resolver = context.tool_definitions_resolver
        tools = resolver() if resolver is not None else []
        if not tools:
            return ProposeStrategyResult.declined(
                "tool_calling_llm",
                reason="no_tools_defined",
            )

        try:
            llm_response = ModelInvocationService.invoke_with_tools(
                prompt=context.base_prompt,
                tools=tools,
                model=None,
            )
        except LLMUnavailableError as exc:
            return ProposeStrategyResult.declined(
                "tool_calling_llm",
                reason=f"llm_required_but_unavailable: {exc}",
            )
        except Exception as exc:
            return ProposeStrategyResult.failed(
                "tool_calling_llm",
                f"llm_call_failed: {exc}",
            )

        tool_calls = llm_response.get("tool_calls") or []
        if tool_calls:
            proposal = ExecutableProposal(
                proposal_id=f"tcllm-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="tool_calling_llm",
                command=None,
                tool_calls=tool_calls,
                expected_artifacts=["workspace-changes"],
                metadata={
                    "provider": provider,
                    "tools_used": [t.get("name") for t in tool_calls if t.get("name")],
                },
            )
            return ProposeStrategyResult.executable("tool_calling_llm", proposal)

        return ProposeStrategyResult.declined(
            "tool_calling_llm",
            reason="llm_returned_no_tool_calls",
        )
