"""ToolCallingLLMStrategy — FA-T009/T021/AFR-T004: real API tools= param."""
from __future__ import annotations

import json

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, ExecutableProposal
from agent.services.model_invocation_service import ModelInvocationService, LLMUnavailableError
from agent.services.llm_response_normalizer import LLMResponseNormalizer

_MOCK_ONLY_PROVIDERS = {"mock"}


def _build_system_prompt(context: ProposeContext) -> str:
    task = context.task or {}
    task_desc = (task.get("description") or task.get("prompt") or "").strip()
    parts = [
        "You are a software engineering agent executing a task.",
        f"Goal: {context.goal_id}",
        f"Task: {context.task_id}",
        f"Task kind: {task.get('task_kind') or 'unknown'}",
    ]
    if task_desc and len(task_desc) > 20:
        parts.append("")
        parts.append("Task description:")
        parts.append(task_desc)
    parts.append("")
    parts.append(
        "You MUST use one or more of the available tools to complete the task. "
        "Return ONLY tool_calls — no prose, no explanations, no markdown. "
        "Each tool_call must specify the function name and its arguments."
    )
    return "\n".join(parts)


class ToolCallingLLMStrategy(ProposeStrategy):
    """Calls a real OpenAI-compatible endpoint with tools= parameter."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        from agent.config import settings
        provider = (settings.default_provider or "lmstudio").strip().lower()

        if provider in _MOCK_ONLY_PROVIDERS:
            return ProposeStrategyResult.declined(
                "tool_calling_llm", reason="provider_tools_not_supported_mock",
            )

        resolver = context.tool_definitions_resolver
        tools = resolver() if resolver is not None else []
        if not tools:
            return ProposeStrategyResult.declined(
                "tool_calling_llm", reason="no_tools_defined",
            )

        try:
            llm_response = ModelInvocationService.invoke_with_tools(
                prompt=context.base_prompt,
                tools=tools,
                model=None,
                system_prompt=_build_system_prompt(context),
            )
        except LLMUnavailableError as exc:
            return ProposeStrategyResult.declined(
                "tool_calling_llm",
                reason=f"llm_required_but_unavailable: {exc}",
                reason_codes=["llm_required", "llm_provider_unavailable"],
            )
        except Exception as exc:
            return ProposeStrategyResult.failed(
                "tool_calling_llm", f"llm_call_failed: {exc}",
            )

        tool_calls = llm_response.get("tool_calls") or []
        content = llm_response.get("content") or ""
        finish_reason = llm_response.get("finish_reason") or ""
        allow_shell_execution = bool(
            getattr(getattr(context, "policy", None), "allow_shell_execution", False)
        )

        # No native tool calls → try to extract from content via normalizer
        if not tool_calls and content.strip():
            normalizer = LLMResponseNormalizer()
            fallback = normalizer.normalize(
                content,
                context,
                allow_shell_execution=allow_shell_execution,
            )
            if isinstance(fallback.metadata, dict):
                fallback.metadata["source"] = "tool_calling_llm_content_fallback"
                fallback.metadata["allow_shell_execution"] = allow_shell_execution
            if fallback.is_executable or fallback.status == "advisory":
                return fallback

        if not tool_calls:
            if finish_reason in ("stop", "length"):
                return ProposeStrategyResult.declined(
                    "tool_calling_llm",
                    reason="tools_not_supported_model_returned_stop",
                    reason_codes=["tools_not_supported"],
                )
            return ProposeStrategyResult.declined(
                "tool_calling_llm", reason="llm_returned_no_tool_calls",
            )

        # Validate tool calls: each must have a name
        valid_tcs = [tc for tc in tool_calls if tc.get("name")]
        if not valid_tcs:
            return ProposeStrategyResult.declined(
                "tool_calling_llm", reason="tool_calls_missing_names",
            )

        proposal = ExecutableProposal(
            proposal_id=f"tcllm-{context.task_id}",
            goal_id=context.goal_id,
            task_id=context.task_id,
            strategy_id="tool_calling_llm",
            command=None,
            tool_calls=valid_tcs,
            expected_artifacts=["workspace-changes"],
            metadata={
                "provider": provider,
                "tools_used": [tc.get("name") for tc in valid_tcs],
            },
        )
        return ProposeStrategyResult.executable("tool_calling_llm", proposal)
