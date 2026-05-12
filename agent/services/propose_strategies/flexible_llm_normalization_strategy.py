"""FlexibleLLMNormalizationStrategy — AFR-T005: policy-aware, all formats."""
from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult
from agent.services.llm_response_normalizer import LLMResponseNormalizer
from agent.services.model_invocation_service import ModelInvocationService, LLMUnavailableError


class FlexibleLLMNormalizationStrategy(ProposeStrategy):
    """Calls any LLM, passes raw output through LLMResponseNormalizer.

    Respects policy.allow_shell_execution: shell blocks are only executable
    when explicitly enabled in policy (default: False → advisory).
    """

    def __init__(self) -> None:
        self._normalizer = LLMResponseNormalizer()

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        try:
            raw = ModelInvocationService.invoke(prompt=context.base_prompt)
        except LLMUnavailableError as exc:
            return ProposeStrategyResult.declined(
                "flexible_llm_normalization",
                reason=f"llm_required_but_unavailable: {exc}",
                reason_codes=["llm_required", "llm_provider_unavailable"],
            )
        except Exception as exc:
            return ProposeStrategyResult.failed(
                "flexible_llm_normalization", f"llm_call_failed: {exc}",
            )

        if not raw or not raw.strip():
            return ProposeStrategyResult.declined(
                "flexible_llm_normalization", reason="llm_returned_empty_response",
            )

        # Determine shell execution policy from context
        allow_shell = False
        if context.policy is not None:
            allow_shell = context.policy.allow_shell_execution

        return self._normalizer.normalize(raw, context, allow_shell_execution=allow_shell)
