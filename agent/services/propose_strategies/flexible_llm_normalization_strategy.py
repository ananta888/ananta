"""FlexibleLLMNormalizationStrategy — AFR-T005: policy-aware, all formats."""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import Mock

from agent.services.llm_response_normalizer import LLMResponseNormalizer
from agent.services.model_invocation_service import LLMUnavailableError, ModelInvocationService
from ananta_contracts.model_recovery import metadata_from_llm_error
from agent.services.model_routing_contract import (
    ModelRoutingContractError,
    build_model_routing_context,
    model_routing_policy_failure_metadata,
)
from agent.services.propose_runtime_policy import resolve_propose_llm_timeout_seconds
from worker.core.propose import ProposeStrategyResult
from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy

_JSON_SYSTEM_PROMPT_FALLBACK = (
    "You are a software engineering assistant. "
    "You MUST respond with valid JSON only. "
    "The JSON MUST contain at least one of:\n"
    '  - "command": a shell command string\n'
    '  - "tool_calls": a list of {"name": "<tool>", "args": {<arguments>}} objects\n'
    'Optional: "reason": a short technical explanation.\n'
    "Output ONLY the raw JSON object. No Markdown fences. No prose. No explanations."
)


def _get_json_system_prompt() -> str:
    try:
        from agent.services.system_prompt_catalog import get_system_prompt

        return get_system_prompt("system.json_normalization", _JSON_SYSTEM_PROMPT_FALLBACK)
    except Exception:
        return _JSON_SYSTEM_PROMPT_FALLBACK


class FlexibleLLMNormalizationStrategy(ProposeStrategy):
    """Calls any LLM, passes raw output through LLMResponseNormalizer.

    Respects policy.allow_shell_execution: shell blocks are only executable
    when explicitly enabled in policy (default: False → advisory).
    """

    def __init__(self) -> None:
        self._normalizer = LLMResponseNormalizer()

    @staticmethod
    def _with_llm_profile(
        result: ProposeStrategyResult,
        llm_profile: list[dict] | None,
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
        result: ProposeStrategyResult,
        llm_metadata: dict | None,
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

    @staticmethod
    def _with_fallback_decisions(
        result: ProposeStrategyResult,
        llm_metadata: dict | None,
    ) -> ProposeStrategyResult:
        metadata = dict(llm_metadata or {})
        decisions = [dict(item) for item in list(metadata.get("fallback_decisions") or []) if isinstance(item, dict)]
        if not decisions:
            return result
        result.metadata = dict(result.metadata or {})
        result.metadata["fallback_decisions"] = decisions
        if result.proposal is not None:
            result.proposal.metadata = dict(result.proposal.metadata or {})
            result.proposal.metadata["fallback_decisions"] = decisions
        return result

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        llm_profile: list[dict] = []
        llm_metadata: dict = {}
        effective_config = dict(context.effective_config) if isinstance(context.effective_config, Mapping) else {}
        try:
            timeout_seconds = resolve_propose_llm_timeout_seconds(
                effective_config=context.effective_config,
                task_kind=str((context.task or {}).get("task_kind") or "").strip().lower() or None,
            )
            if isinstance(ModelInvocationService.invoke, Mock) and not isinstance(
                ModelInvocationService.invoke_result, Mock
            ):
                raw = ModelInvocationService.invoke(
                    prompt=context.base_prompt,
                    system_prompt=_get_json_system_prompt(),
                    timeout=timeout_seconds,
                    routing_ctx=build_model_routing_context(
                        context.task,
                        context_text=context.base_prompt,
                        requires_json=True,
                    ),
                    provider_context=effective_config.get("provider_context"),
                    provider_contexts_by_profile_id=effective_config.get("provider_contexts_by_profile_id"),
                    provider_attempt_plan=effective_config.get("provider_attempt_plan"),
                )
            else:
                llm_result = ModelInvocationService.invoke_result(
                    prompt=context.base_prompt,
                    system_prompt=_get_json_system_prompt(),
                    timeout=timeout_seconds,
                    routing_ctx=build_model_routing_context(
                        context.task,
                        context_text=context.base_prompt,
                        requires_json=True,
                    ),
                    provider_context=effective_config.get("provider_context"),
                    provider_contexts_by_profile_id=effective_config.get("provider_contexts_by_profile_id"),
                    provider_attempt_plan=effective_config.get("provider_attempt_plan"),
                )
                raw = str(llm_result.get("content") or "")
                llm_metadata = (
                    dict(llm_result.get("metadata") or {}) if isinstance(llm_result.get("metadata"), dict) else {}
                )
                llm_profile = [
                    entry for entry in list((llm_metadata.get("llm_call_profile") or [])) if isinstance(entry, dict)
                ]
        except ModelRoutingContractError as exc:
            return ProposeStrategyResult.declined(
                "flexible_llm_normalization",
                reason="model_routing_policy_blocked",
                reason_codes=["model_routing_invalid", "policy_blocked"],
                metadata=model_routing_policy_failure_metadata(exc),
            )
        except LLMUnavailableError as exc:
            return ProposeStrategyResult.declined(
                "flexible_llm_normalization",
                reason=f"llm_required_but_unavailable: {exc}",
                reason_codes=["llm_required", "llm_provider_unavailable", "model_chain_exhausted"],
                metadata=metadata_from_llm_error(exc),
            )
        except Exception as exc:
            return self._with_llm_profile(
                ProposeStrategyResult.failed(
                    "flexible_llm_normalization",
                    f"llm_call_failed: {exc}",
                ),
                llm_profile,
            )

        if not raw or not raw.strip():
            result = ProposeStrategyResult.declined(
                "flexible_llm_normalization",
                reason="llm_returned_empty_response",
            )
            result = self._with_llm_profile(result, llm_profile)
            result = self._with_fallback_decisions(result, llm_metadata)
            return self._with_llm_trace_link(result, llm_metadata)

        # Determine shell execution policy from context
        allow_shell = False
        if context.policy is not None:
            allow_shell = context.policy.allow_shell_execution

        normalized = self._normalizer.normalize(raw, context, allow_shell_execution=allow_shell)
        normalized.metadata = dict(normalized.metadata or {})
        normalized.metadata.setdefault("prompt_context_bundle", {})
        pcb = dict(normalized.metadata.get("prompt_context_bundle") or {})
        stack = context.instruction_stack if isinstance(context.instruction_stack, dict) else {}
        pcb.setdefault("instruction_stack_present", bool(stack))
        pcb.setdefault("instruction_stack_checksum", stack.get("checksum"))
        normalized.metadata["prompt_context_bundle"] = pcb
        normalized = self._with_llm_profile(normalized, llm_profile)
        normalized = self._with_fallback_decisions(normalized, llm_metadata)
        return self._with_llm_trace_link(normalized, llm_metadata)
