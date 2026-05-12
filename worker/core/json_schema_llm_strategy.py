"""JsonSchemaLLMStrategy — FA-T009 response_format json_schema."""

from __future__ import annotations

import json

from typing import Dict, Any

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy

from worker.core.propose import (
    ProposeStrategyResult,
    ExecutableProposal,
)

from agent.services.model_invocation_service import ModelInvocationService

class JsonSchemaLLMStrategy(ProposeStrategy):
    """Strategy using response_format={'type': 'json_object'} or json_schema."""

    SUPPORTED_PROVIDERS = {"openai", "google", "azure_openai"}

    JSON_SCHEMA = {
        "type": "object",
        "properties": {
            "command": {"type": ["string", "null"]},
            "tool_calls": {
                "type": "array",
                "items": {"type": "object"},
            },
        },
        "required": [],  # optional
        "additionalProperties": False,
    }

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        try:
            tools = context.tool_definitions_resolver() or []
            if not tools:
                return ProposeStrategyResult.declined(
                    "json_schema_llm",
                    reason="no_tools_defined",
                )

            provider = "openai"  # mock

            if provider not in self.SUPPORTED_PROVIDERS:
                return ProposeStrategyResult.declined(
                    "json_schema_llm",
                    reason="provider_json_schema_not_supported",
                )

            raw_response = ModelInvocationService.invoke_with_json_schema(
                prompt=context.base_prompt,
                json_schema=self.JSON_SCHEMA,
                model="gpt-4o",
            )

            parsed = json.loads(raw_response)

            tool_calls = parsed.get("tool_calls", [])
            command = parsed.get("command")

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
            elif command:
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
            else:
                return ProposeStrategyResult.advisory(
                    "json_schema_llm",
                    advisory_text=json.dumps(parsed),
                )
        except json.JSONDecodeError:
            return ProposeStrategyResult.advisory(
                "json_schema_llm",
                advisory_text=raw_response[:200] + "..." if len(raw_response) > 200 else raw_response,
            )
        except Exception as e:
            return ProposeStrategyResult.failed(
                "json_schema_llm",
                f"llm_call_failed: {str(e)}",
            )
