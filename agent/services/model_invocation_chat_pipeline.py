"""Chat call pipeline of ModelInvocationService: resolve candidate profiles
and run attempts with Hub-governed retry and fallback decisions."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from agent.services.model_invocation_errors import (
    LLMUnavailableError,
    ModelRoutingConfigurationError,
)

# Shares the historical logger channel so log routing/filters stay unchanged.
logger = logging.getLogger("agent.services.model_invocation_service")


class ModelInvocationChatPipelineMixin:
    """Drive the attempt chain for one logical chat request."""

    @classmethod
    def _make_chat_call(
        cls,
        messages: list[dict],
        *,
        tools: list | None = None,
        response_format: dict | None = None,
        response_validator: Callable[[dict[str, Any]], None] | None = None,
        model: str | None = None,
        timeout: int | None = None,
        routing_ctx: Any = None,
        provider_context: Any = None,
        provider_contexts_by_profile_id: Mapping[str, Any] | None = None,
        provider_attempt_plan: Any = None,
    ) -> dict:
        resolver = None
        resolution_result = None
        candidate_profiles: list[Any] = []
        resolution_info: dict[str, Any] = {}
        explicit_routing = routing_ctx is not None
        signed_attempt_plan = cls._validated_provider_attempt_plan(provider_attempt_plan)
        try:
            resolver = cls._get_resolver()
            requested_model_override = str(model or "").strip()
            if resolver is not None and requested_model_override and requested_model_override != "auto":
                raise cls._routing_policy_blocked_error("model_override_not_allowed_with_profile_routing")
            if routing_ctx is None and resolver is not None:
                from agent.services.model_profile_resolver import RoutingContext

                routing_ctx = RoutingContext(
                    model_role="any",
                    context_text="\n".join(
                        str(message.get("content") or "") for message in messages if isinstance(message, dict)
                    ),
                    allow_cloud=False,
                )
            if signed_attempt_plan and resolver is not None:
                (
                    candidate_profiles,
                    resolution_info,
                ) = cls._profiles_for_signed_attempt_plan(
                    resolver,
                    signed_attempt_plan,
                )
            elif routing_ctx is not None and resolver is not None:
                resolution_result, candidate_profiles = resolver.resolve_candidate_chain(routing_ctx)
                if resolution_result.ok:
                    resolution_info = {
                        "profile_id": resolution_result.profile.profile_id,
                        "initial_profile_id": resolution_result.profile.profile_id,
                        "resolution_source": resolution_result.final_source,
                        "resolution_rank": resolution_result.final_rank,
                        "candidate_chain": [profile.profile_id for profile in candidate_profiles],
                    }
                else:
                    resolution_info = {
                        "resolution_source": "none",
                        "resolution_fallback_reason": "no_profile_resolved",
                        "blocked_candidates": [reason for _, reason in resolution_result.blocked_candidates],
                    }
        except ModelRoutingConfigurationError as exc:
            logger.warning("model_invocation: configured routing unavailable: %s", exc)
            raise cls._configured_routing_unavailable_error() from exc
        except LLMUnavailableError:
            raise
        except Exception as exc:
            resolution_info = {
                "resolution_source": "error",
                "resolution_fallback_reason": f"resolver_error:{type(exc).__name__}",
            }
            logger.warning("model_invocation: resolver failed: %s", exc)
            if explicit_routing or signed_attempt_plan:
                raise cls._configured_routing_unavailable_error() from exc

        if resolver is None and (
            signed_attempt_plan or explicit_routing or cls._model_routing_configuration_requested()
        ):
            raise cls._configured_routing_unavailable_error()

        if (
            not signed_attempt_plan
            and routing_ctx is not None
            and resolver is not None
            and (resolution_result is None or not resolution_result.ok)
        ):
            decision_reasons = [
                str(getattr(decision, "reason", "") or "").strip().lower()
                for decision in list(getattr(resolution_result, "decisions", []) or [])
            ]
            if any("context_too_large" in reason for reason in decision_reasons):
                terminal_reason = "context_too_large"
            elif any("provider_health:unavailable" in reason for reason in decision_reasons):
                terminal_reason = "provider_unavailable"
            else:
                terminal_reason = "policy_blocked"
            raise LLMUnavailableError(
                f"model_routing_exhausted:{terminal_reason}",
                fallback_decisions=[
                    {
                        "reason": "model_routing_candidate_chain_exhausted",
                        "previous_profile_id": None,
                        "next_profile_id": None,
                        "trigger": terminal_reason,
                        "terminal": True,
                        "blocked_candidates": cls._blocked_candidates_as_dict(
                            getattr(
                                resolution_result,
                                "blocked_candidates",
                                [],
                            )
                        ),
                    }
                ],
                terminal_reason=terminal_reason,
            )

        attempts: list[dict[str, Any]] = []
        if candidate_profiles:
            for profile in candidate_profiles:
                provider, url, api_key = cls._provider_info_from_profile(profile)
                effective_model = (
                    profile.model if profile.model and profile.model != "auto" else cls._get_settings().default_model
                )
                attempts.append(
                    {
                        "profile": profile,
                        "provider": provider,
                        "url": url,
                        "api_key": api_key,
                        "model": effective_model,
                        "timeout": timeout if timeout is not None else profile.timeout_seconds,
                    }
                )
        else:
            provider, url, api_key = cls._provider_info()
            settings = cls._get_settings()
            attempts.append(
                {
                    "profile": None,
                    "provider": provider,
                    "url": url,
                    "api_key": api_key,
                    "model": model if model and model != "auto" else settings.default_model,
                    "timeout": timeout
                    if timeout is not None
                    else int(getattr(settings, "llm_invoke_timeout_seconds", None) or 120),
                }
            )
            resolution_info.setdefault("resolution_source", "legacy_provider_info")

        from agent.services.model_fallback_policy_service import ModelFallbackPolicyService

        call_profile: list[dict[str, Any]] = []
        fallback_decisions: list[dict[str, Any]] = []
        blocked = cls._blocked_candidates_as_dict(getattr(resolution_result, "blocked_candidates", []))
        fallback_policy = ModelFallbackPolicyService(
            getattr(resolver, "health", None) if resolver is not None else None
        )
        fallback_group_rule = None
        if (
            not signed_attempt_plan
            and resolver is not None
            and routing_ctx is not None
            and hasattr(resolver, "fallback_group_rule_for_context")
        ):
            fallback_group_rule = resolver.fallback_group_rule_for_context(
                routing_ctx,
                (
                    resolution_result.profile.profile_id
                    if resolution_result is not None and resolution_result.profile is not None
                    else None
                ),
            )
        group_retry_budget = (
            max(0, int(routing_ctx.fallback_max_total_retries))
            if routing_ctx is not None and getattr(routing_ctx, "fallback_max_total_retries", None) is not None
            else (max(0, int(fallback_group_rule.max_total_retries)) if fallback_group_rule is not None else None)
        )
        if group_retry_budget is not None:
            resolution_info["fallback_group_max_total_retries"] = group_retry_budget
        same_profile_retries_used = 0
        request_attempt = 0

        for index, attempt in enumerate(attempts):
            failed_attempts = 0
            while True:
                try:
                    attempt_resolution_info = dict(resolution_info)
                    attempt_profile = attempt.get("profile")
                    if attempt_profile is not None:
                        attempt_resolution_info.update(
                            {
                                "profile_id": attempt_profile.profile_id,
                                "provider_id": attempt["provider"],
                                "model": attempt["model"],
                                "fallback_index": index,
                            }
                        )
                    request_provider_context = cls._provider_context_for_request(
                        provider_context=provider_context,
                        provider_contexts_by_profile_id=(provider_contexts_by_profile_id),
                        profile=attempt_profile,
                        profile_index=index,
                        provider=attempt["provider"],
                        model=attempt["model"],
                        request_attempt=request_attempt,
                    )
                    payload = cls._make_single_chat_call(
                        messages,
                        tools=tools,
                        response_format=response_format,
                        response_validator=response_validator,
                        attempt=attempt,
                        resolution_info=attempt_resolution_info,
                        provider_context=request_provider_context,
                    )
                    request_attempt += 1
                    payload = cls._decorate_invocation_payload(
                        payload,
                        call_profile=call_profile,
                        fallback_decisions=fallback_decisions,
                        resolution_info=attempt_resolution_info,
                    )
                    cls._observe_successful_model_invocation_attempt(
                        payload=payload,
                        attempt=attempt,
                        resolution_info=attempt_resolution_info,
                    )
                    return payload
                except LLMUnavailableError as exc:
                    request_attempt += 1
                    call_profile.extend(entry for entry in list(exc.llm_call_profile or []) if isinstance(entry, dict))
                    for nested_decision in list(getattr(exc, "fallback_decisions", []) or []):
                        if isinstance(nested_decision, dict) and nested_decision not in fallback_decisions:
                            fallback_decisions.append(dict(nested_decision))
                    failed_attempts += 1
                    error_type = cls._fallback_error_type(exc)
                    cls._observe_failed_model_invocation_attempt(
                        error=exc,
                        error_type=error_type,
                        attempt=attempt,
                        resolution_info=attempt_resolution_info,
                    )
                    if signed_attempt_plan:
                        action = cls._signed_attempt_failure_action(
                            signed_attempt_plan=signed_attempt_plan,
                            index=index,
                            failed_attempts=failed_attempts,
                            error_type=error_type,
                            fallback_policy=fallback_policy,
                            fallback_decisions=fallback_decisions,
                            call_profile=call_profile,
                            error=exc,
                        )
                        if action == "retry":
                            continue
                        break
                    if not fallback_policy.candidate_allows_trigger(attempt.get("profile"), error_type):
                        fallback_decisions.append(
                            {
                                "reason": "candidate_trigger_not_allowed",
                                "previous_profile_id": getattr(attempt.get("profile"), "profile_id", None),
                                "next_profile_id": None,
                                "trigger": error_type,
                                "terminal": True,
                            }
                        )
                        raise LLMUnavailableError(
                            str(exc),
                            llm_call_profile=call_profile,
                            fallback_decisions=fallback_decisions,
                            terminal_reason=error_type,
                        )
                    profile_retry_allowed = fallback_policy.should_retry_profile(
                        error_type=error_type,
                        profile=attempt.get("profile"),
                        failed_attempts=failed_attempts,
                    )
                    group_retry_allowed = group_retry_budget is None or same_profile_retries_used < group_retry_budget
                    if profile_retry_allowed and group_retry_allowed:
                        same_profile_retries_used += 1
                        fallback_decisions.append(
                            {
                                "reason": "same_profile_retry_allowed",
                                "previous_profile_id": getattr(attempt.get("profile"), "profile_id", None),
                                "next_profile_id": getattr(attempt.get("profile"), "profile_id", None),
                                "trigger": error_type,
                                "failed_attempts": failed_attempts,
                                "group_retries_used": same_profile_retries_used,
                                "terminal": False,
                            }
                        )
                        logger.warning(
                            "model_invocation: retry profile=%s failed_attempts=%s trigger=%s",
                            getattr(attempt.get("profile"), "profile_id", None),
                            failed_attempts,
                            error_type,
                        )
                        continue
                    next_profile = attempts[index + 1]["profile"] if index + 1 < len(attempts) else None
                    decision = fallback_policy.should_fallback(
                        error_type=error_type,
                        previous_profile=attempt.get("profile"),
                        next_profile=next_profile,
                        blocked_candidates=blocked,
                    )
                    fallback_decisions.append(decision.as_dict())
                    if decision.terminal:
                        raise LLMUnavailableError(
                            str(exc),
                            llm_call_profile=call_profile,
                            fallback_decisions=fallback_decisions,
                            terminal_reason=error_type,
                        )
                    logger.warning(
                        "model_invocation: fallback %s -> %s trigger=%s",
                        decision.previous_profile_id,
                        decision.next_profile_id,
                        decision.trigger,
                    )
                    break

        raise LLMUnavailableError(
            "llm_unavailable:no_attempts",
            llm_call_profile=call_profile,
            fallback_decisions=fallback_decisions,
            terminal_reason="no_attempts",
        )
