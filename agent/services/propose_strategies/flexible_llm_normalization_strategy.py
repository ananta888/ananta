"""FlexibleLLMNormalizationStrategy — AFR-T005: policy-aware, all formats."""
from __future__ import annotations

import json

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult
from agent.services.llm_response_normalizer import LLMResponseNormalizer
from agent.services.model_invocation_service import ModelInvocationService, LLMUnavailableError
from agent.services.propose_runtime_policy import resolve_propose_llm_timeout_seconds

_JSON_SYSTEM_PROMPT = (
    "You are a software engineering assistant. "
    "You MUST respond with valid JSON only. "
    "The JSON MUST contain at least one of:\n"
    '  - "command": a shell command string\n'
    '  - "tool_calls": a list of {"name": "<tool>", "args": {<arguments>}} objects\n'
    'Optional: "reason": a short technical explanation.\n'
    "Examples:\n"
    '  {"reason": "list directory", "command": "ls -la"}\n'
    '  {"reason": "create file", "tool_calls": [{"name": "write_file", "args": {"path": "test.txt", "content": "hello"}}]}\n'
    "Output ONLY the raw JSON object. No Markdown fences. No prose. No explanations."
)


class FlexibleLLMNormalizationStrategy(ProposeStrategy):
    """Calls any LLM, passes raw output through LLMResponseNormalizer.

    Respects policy.allow_shell_execution: shell blocks are only executable
    when explicitly enabled in policy (default: False → advisory).
    """

    def __init__(self) -> None:
        self._normalizer = LLMResponseNormalizer()

    @staticmethod
    def _with_llm_profile(
        result: ProposeStrategyResult, llm_profile: list[dict] | None,
    ) -> ProposeStrategyResult:
        profile = [entry for entry in list(llm_profile or []) if isinstance(entry, dict)]
        if not profile:
            return result
        result.metadata = dict(result.metadata or {})
        result.metadata["llm_call_profile"] = profile
        if result.proposal is not None:
            result.proposal.metadata = dict(result.proposal.metadata or {})
            result.proposal.metadata["llm_call_profile"] = profile
        return result

    @staticmethod
    def _with_llm_trace_link(
        result: ProposeStrategyResult, llm_metadata: dict | None,
    ) -> ProposeStrategyResult:
        metadata = dict(llm_metadata or {})
        prompt_trace_id = str(metadata.get("prompt_trace_id") or "").strip()
        if not prompt_trace_id:
            return result
        result.metadata = dict(result.metadata or {})
        result.metadata["prompt_trace_id"] = prompt_trace_id
        if result.proposal is not None:
            result.proposal.metadata = dict(result.proposal.metadata or {})
            result.proposal.metadata["prompt_trace_id"] = prompt_trace_id
        return result

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        llm_profile: list[dict] = []
        llm_metadata: dict = {}
        try:
            timeout_seconds = resolve_propose_llm_timeout_seconds(
                effective_config=context.effective_config,
                task_kind=str((context.task or {}).get("task_kind") or "").strip().lower() or None,
            )
            llm_result = ModelInvocationService.invoke_result(
                prompt=context.base_prompt,
                system_prompt=_JSON_SYSTEM_PROMPT,
                timeout=timeout_seconds,
            )
            raw = str(llm_result.get("content") or "")
            llm_metadata = dict(llm_result.get("metadata") or {}) if isinstance(llm_result.get("metadata"), dict) else {}
            llm_profile = [
                entry
                for entry in list((llm_metadata.get("llm_call_profile") or []))
                if isinstance(entry, dict)
            ]
        except LLMUnavailableError as exc:
            llm_profile = [entry for entry in list(getattr(exc, "llm_call_profile", []) or []) if isinstance(entry, dict)]
            return self._with_llm_profile(ProposeStrategyResult.declined(
                "flexible_llm_normalization",
                reason=f"llm_required_but_unavailable: {exc}",
                reason_codes=["llm_required", "llm_provider_unavailable"],
            ), llm_profile)
        except Exception as exc:
            return self._with_llm_profile(ProposeStrategyResult.failed(
                "flexible_llm_normalization", f"llm_call_failed: {exc}",
            ), llm_profile)

        if not raw or not raw.strip():
            result = ProposeStrategyResult.declined(
                "flexible_llm_normalization", reason="llm_returned_empty_response",
            )
            result = self._with_llm_profile(result, llm_profile)
            return self._with_llm_trace_link(result, llm_metadata)

        # Determine shell execution policy from context
        allow_shell = False
        if context.policy is not None:
            allow_shell = context.policy.allow_shell_execution

        normalized = self._normalizer.normalize(raw, context, allow_shell_execution=allow_shell)
        normalized = self._with_llm_profile(normalized, llm_profile)
        return self._with_llm_trace_link(normalized, llm_metadata)
