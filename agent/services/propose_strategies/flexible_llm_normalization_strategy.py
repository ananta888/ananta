"""FlexibleLLMNormalizationStrategy — FA-T021: any LLM output, all formats."""
from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult
from agent.services.llm_response_normalizer import LLMResponseNormalizer
from agent.services.model_invocation_service import ModelInvocationService, LLMUnavailableError


class FlexibleLLMNormalizationStrategy(ProposeStrategy):
    """Calls any LLM, accepts any output format, normalizes via LLMResponseNormalizer.

    Last LLM-based attempt before advisory_proposal. Accepts prose, JSON, shell,
    diffs, file blocks — whatever the model returns.
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
            )
        except Exception as exc:
            return ProposeStrategyResult.failed(
                "flexible_llm_normalization",
                f"llm_call_failed: {exc}",
            )

        if not raw or not raw.strip():
            return ProposeStrategyResult.declined(
                "flexible_llm_normalization",
                reason="llm_returned_empty_response",
            )

        return self._normalizer.normalize(raw, context)
