"""ToolCallingLLMStrategy — FA-T009 first-class model API tool calling, no sgpt."""

from __future__ import annotations

from typing import List, Dict, Any

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy

from worker.core.propose import (
    ProposeStrategyResult,
    ExecutableProposal,
)

class ToolCallingLLMStrategy(ProposeStrategy):
    """Strategy using real API tools= param."""

    SUPPORTED_PROVIDERS = {"openai", "anthropic", "google", "azure_openai"}

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        try:
            tools = context.tool_definitions_resolver() or []
            if not tools:
                return ProposeStrategyResult.declined(
                    "tool_calling_llm",
                    reason="no_tools_defined",
                )

            # Resolve provider from policy/context (TODO: ProposePolicy.llm_provider)
            provider = "openai"  # mock

            if provider not in self.SUPPORTED_PROVIDERS:
                return ProposeStrategyResult.declined(
                    "tool_calling_llm",
                    reason="provider_tools_not_supported",
                )

            # TODO: Real LLM call via model_invocation_service
            # llm_response = ModelInvocationService.invoke_with_tools(
            #     prompt=context.base_prompt,
            #     tools=tools,
            #     model=policy.llm_model,
            #     task=context.task,
            # )

            from agent.services.model_invocation_service import ModelInvocationService

            llm_response = ModelInvocationService.invoke_with_tools(
                prompt=context.base_prompt,
                tools=tools,
                model="gpt-4o-mini-tools",
            )

            tool_calls = llm_response.get("tool_calls", [])
            if tool_calls:
                # Filter invalid tools? TODO
                proposal = ExecutableProposal(
                    proposal_id=f"tcllm-{context.task_id}",
                    goal_id=context.goal_id,
                    task_id=context.task_id,
                    strategy_id="tool_calling_llm",
                    command=None,
                    tool_calls=tool_calls,
                    expected_artifacts=["api_server", "tests"],
                    metadata={
                        "provider": provider,
                        "tools_used": [t["name"] for t in tool_calls],
                    },
                )
                return ProposeStrategyResult.executable("tool_calling_llm", proposal)
            else:
                return ProposeStrategyResult.advisory(
                    "tool_calling_llm",
                    advisory_text=llm_response.get("content", "No tool calls."),
                )
        except Exception as e:
            return ProposeStrategyResult.failed(
                "tool_calling_llm",
                f"llm_call_failed: {str(e)}",
            )
