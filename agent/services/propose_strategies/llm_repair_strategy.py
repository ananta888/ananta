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
        # FA-T011: bounded single repair attempt per strategy invocation.
        raw_max_repairs = getattr(getattr(context, "policy", None), "max_repair_attempts", 1)
        max_repairs = int(1 if raw_max_repairs is None else raw_max_repairs)
        if max_repairs <= 0:
            return ProposeStrategyResult.declined(
                "llm_repair_strategy",
                reason="repair_disabled_by_policy",
                reason_codes=["repair_disabled"],
            )
        previous_raw = ""
        validation_errors = "invalid or non-executable output"
        if isinstance(context.research_context, dict):
            previous_raw = str(context.research_context.get("raw_output") or "")[:5000]
            validation_errors = str(context.research_context.get("validation_errors") or validation_errors)
        if not previous_raw:
            previous_raw = "No raw output provided"

        # Schema stub (real: ExecutableProposal schema)
        schema_example = """
{
  \"command\": \"string or null\",
  \"tool_calls\": [{\"name\": \"string\", \"args\": {}}]
}
"""

        resolver = context.tool_definitions_resolver
        tools = resolver() if resolver is not None else []

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
- Respect shell policy: allow_shell_execution={getattr(getattr(context, "policy", None), "allow_shell_execution", False)}
- No prose, no Markdown, no explanations.

Respond with ONLY the fixed JSON."""

        try:
            repair_output = self.model_service.invoke(
                prompt=repair_prompt,
                model=None,
            )
            normalized = self.normalizer.normalize(
                repair_output,
                context,
                allow_shell_execution=bool(
                    getattr(getattr(context, "policy", None), "allow_shell_execution", False)
                ),
            )

            if normalized.is_executable:
                if isinstance(normalized.metadata, dict):
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
