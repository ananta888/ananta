"""LLMRepairStrategy — FA-T011 bounded repair/coercion for malformed LLM outputs."""

from __future__ import annotations

from typing import Any

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, STATUS_DECLINED, STATUS_FAILED

from agent.services.llm_response_normalizer import LLMResponseNormalizer
from agent.services.model_invocation_service import ModelInvocationService


class LLMRepairStrategy(ProposeStrategy):
    """Repair strategy: LLM call to fix malformed output, then normalize."""

    def __init__(self):
        self.normalizer = LLMResponseNormalizer()
        self.model_service = ModelInvocationService

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        # Stub previous failed output (in real: from context.previous_result.raw_output)
        previous_raw = "```json\n{ 'tool_calls': [ { 'name': 'write_file' } ] \n```  # malformed"
        validation_errors = "missing args in tool_call, invalid JSON"

        # Schema stub (real: ExecutableProposal schema)
        schema_example = """
{
  \"command\": \"string or null\",
  \"tool_calls\": [{\"name\": \"string\", \"args\": {}}]
}
"""

        tools = context.tool_definitions_resolver() or []

        repair_prompt = f"""You are a repair agent. Fix this LLM output to a valid ExecutableProposal.

Previous output:
{previous_raw}

Validation errors:
{validation_errors}

Target format (JSON schema example):
{schema_example}

Allowed tools only:
{tools}

Rules:
- Output ONLY valid JSON matching schema (command or tool_calls).
- Do not invent new tools or commands.
- Do not broaden permissions.
- No prose, no Markdown, no explanations.

Respond with ONLY the fixed JSON."""

        try:
            repair_output = self.model_service.invoke(
                prompt=repair_prompt,
                model="gpt-4o-mini",
            )
            normalized = self.normalizer.normalize(repair_output, context)

            if normalized.is_executable:
                normalized.metadata["repair_attempted"] = True
                normalized.metadata["repair_success"] = True
                normalized.reason_codes.append("repair_success")
                return normalized
            else:
                return ProposeStrategyResult(
                    status=STATUS_DECLINED,
                    strategy_id="llm_repair_strategy",
                    reason="repair_normalization_failed",
                    reason_codes=["repair_failed", normalized.reason or "unknown"],
                    metadata={"repair_attempted": True, "repair_success": False, "repair_output_preview": repair_output[:200]}
                )
        except Exception as e:
            return ProposeStrategyResult(
                status=STATUS_FAILED,
                strategy_id="llm_repair_strategy",
                reason=f"repair_call_failed: {str(e)}",
                reason_codes=["repair_call_failed"],
                metadata={"repair_attempted": True, "repair_success": False}
            )
