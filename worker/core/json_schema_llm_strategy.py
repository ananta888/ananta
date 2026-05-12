"""JsonSchemaLLMStrategy — FA-T009/T021: response_format=json_object, no sgpt."""
from __future__ import annotations

import json

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, ExecutableProposal
from agent.services.model_invocation_service import ModelInvocationService, LLMUnavailableError

_MOCK_ONLY_PROVIDERS = {"mock"}


class JsonSchemaLLMStrategy(ProposeStrategy):
    """Strategy using response_format=json_object on an OpenAI-compatible endpoint."""

    JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "command": {"type": ["string", "null"]},
            "tool_calls": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": False,
    }

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        from agent.config import settings
        provider = (settings.default_provider or "lmstudio").strip().lower()

        if provider in _MOCK_ONLY_PROVIDERS:
            return ProposeStrategyResult.declined(
                "json_schema_llm",
                reason="provider_json_schema_not_supported_mock",
            )

        try:
            raw_response = ModelInvocationService.invoke_with_json_schema(
                prompt=context.base_prompt,
                json_schema=self.JSON_SCHEMA,
                model=None,
            )
        except LLMUnavailableError as exc:
            return ProposeStrategyResult.declined(
                "json_schema_llm",
                reason=f"llm_required_but_unavailable: {exc}",
            )
        except Exception as exc:
            return ProposeStrategyResult.failed(
                "json_schema_llm",
                f"llm_call_failed: {exc}",
            )

        if not raw_response or not raw_response.strip():
            return ProposeStrategyResult.declined(
                "json_schema_llm",
                reason="llm_returned_empty_response",
            )

        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            return ProposeStrategyResult.advisory(
                "json_schema_llm",
                advisory_text=raw_response[:200],
                reason="json_parse_failed",
            )

        tool_calls = parsed.get("tool_calls") or []
        command = parsed.get("command") or None

        if tool_calls:
            proposal = ExecutableProposal(
                proposal_id=f"jsllm-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="json_schema_llm",
                command=None,
                tool_calls=tool_calls,
                expected_artifacts=["workspace-changes"],
                metadata={"provider": provider},
            )
            return ProposeStrategyResult.executable("json_schema_llm", proposal)

        if command:
            proposal = ExecutableProposal(
                proposal_id=f"jsllm-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="json_schema_llm",
                command=command,
                tool_calls=[],
                expected_artifacts=["command_output"],
                metadata={"provider": provider},
            )
            return ProposeStrategyResult.executable("json_schema_llm", proposal)

        return ProposeStrategyResult.declined(
            "json_schema_llm",
            reason="llm_returned_no_executable_output",
        )
