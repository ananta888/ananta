"""JsonSchemaLLMStrategy — FA-T009/T021/AFR-T004: response_format=json_object."""
from __future__ import annotations

import json

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, ExecutableProposal
from agent.services.model_invocation_service import ModelInvocationService, LLMUnavailableError

_MOCK_ONLY_PROVIDERS = {"mock"}

_SCHEMA_SYSTEM_PROMPT = """You are a structured output generator.
You MUST respond with valid JSON only — no prose, no markdown, no explanations.

The JSON must match this schema:
{
  "command": "<shell command string, or null>",
  "tool_calls": [{"name": "<tool_name>", "args": {<arguments>}}]
}

Rules:
- Include at least one of "command" or "tool_calls".
- "reason" is optional but recommended.
- Output ONLY the raw JSON object. No fences. No text before or after."""

_SCHEMA_PROMPT_SUFFIX = """
Respond with valid JSON:
{"command": "...", "tool_calls": [], "reason": "..."}
or {"command": null, "tool_calls": [{"name": "...", "args": {...}}], "reason": "..."}
Only raw JSON. No prose. No markdown."""


class JsonSchemaLLMStrategy(ProposeStrategy):
    """Calls LLM with response_format=json_object, parses command/tool_calls."""

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
                "json_schema_llm", reason="provider_json_schema_not_supported_mock",
            )

        prompt = context.base_prompt + _SCHEMA_PROMPT_SUFFIX

        try:
            raw_response = ModelInvocationService.invoke_with_json_schema(
                prompt=prompt,
                json_schema=self.JSON_SCHEMA,
                model=None,
                system_prompt=_SCHEMA_SYSTEM_PROMPT,
            )
        except LLMUnavailableError as exc:
            return ProposeStrategyResult.declined(
                "json_schema_llm",
                reason=f"llm_required_but_unavailable: {exc}",
                reason_codes=["llm_required", "llm_provider_unavailable"],
            )
        except Exception as exc:
            return ProposeStrategyResult.failed(
                "json_schema_llm", f"llm_call_failed: {exc}",
            )

        if not raw_response or not raw_response.strip():
            return ProposeStrategyResult.declined(
                "json_schema_llm", reason="llm_returned_empty_response",
            )

        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            return ProposeStrategyResult.advisory(
                "json_schema_llm",
                advisory_text=raw_response[:300],
                reason="json_parse_failed",
                reason_codes=["json_parse_failed"],
            )

        if not isinstance(parsed, dict):
            return ProposeStrategyResult.advisory(
                "json_schema_llm",
                advisory_text=str(parsed)[:300],
                reason="json_not_object",
            )

        tool_calls = parsed.get("tool_calls") or []
        command = parsed.get("command") or None
        if command:
            command = str(command).strip() or None

        # Validate tool calls
        valid_tcs = [tc for tc in tool_calls if isinstance(tc, dict) and tc.get("name")]

        if valid_tcs:
            proposal = ExecutableProposal(
                proposal_id=f"jsllm-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="json_schema_llm",
                command=None,
                tool_calls=valid_tcs,
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
            "json_schema_llm", reason="llm_returned_no_executable_output",
        )
