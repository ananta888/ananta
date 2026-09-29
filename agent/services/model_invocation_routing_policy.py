"""Routing policy of ModelInvocationService: Hub-bound provider contexts,
signed provider attempt plans and their fallback/retry decisions."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from agent.services.model_invocation_errors import (
    LLMUnavailableError,
    ModelRoutingConfigurationError,
)
from agent.services.model_invocation_support import (
    composed_model_invocation_service,
)
from ananta_contracts.provider_endpoint_policy import (
    normalize_provider_endpoint_identity,
)


class ModelInvocationRoutingPolicyMixin:
    """Enforce Hub-owned provider bindings; never select a provider on its own."""

    @staticmethod
    def _model_routing_configuration_requested() -> bool:
        import os

        return any(
            str(os.environ.get(name) or "").strip()
            for name in (
                "MODEL_PROFILES_PATH",
                "MODEL_ROUTING_PATH",
                "ANANTA_MODEL_ROUTING_PATH",
            )
        )

    @staticmethod
    def _configured_routing_unavailable_error() -> LLMUnavailableError:
        return composed_model_invocation_service()._routing_policy_blocked_error(
            "configured_model_routing_unavailable"
        )

    @staticmethod
    def _routing_policy_blocked_error(reason: str) -> LLMUnavailableError:
        normalized_reason = str(reason or "model_routing_policy_blocked").strip()[:160]
        return LLMUnavailableError(
            normalized_reason,
            fallback_decisions=[
                {
                    "reason": normalized_reason,
                    "previous_profile_id": None,
                    "next_profile_id": None,
                    "trigger": "policy_blocked",
                    "terminal": True,
                }
            ],
            terminal_reason="policy_blocked",
        )

    @classmethod
    def _provider_context_for_request(
        cls,
        *,
        provider_context: Any,
        provider_contexts_by_profile_id: Mapping[str, Any] | None,
        profile: Any,
        profile_index: int,
        provider: str,
        model: str,
        request_attempt: int,
    ) -> Any:
        """Advance retry state and select only an already Hub-bound fallback.

        A Worker never rewrites a signed provider selection.  A fallback may
        use a different provider/model only when the caller supplied a
        separate context for that exact profile.
        """

        from ananta_contracts.provider_invocation import (
            ProviderInvocationBlocked,
            ProviderInvocationContext,
        )

        try:
            primary = ProviderInvocationContext.from_value(provider_context)
            primary.assert_valid()
            candidate = primary
            primary_matches = not primary.selected_provider_id or (
                primary.selected_provider_id == provider and primary.selected_model_id == model
            )
            profile_id = str(getattr(profile, "profile_id", "") or "").strip()
            if not primary_matches:
                if profile_index < 1:
                    raise ProviderInvocationBlocked("provider_selection_binding_mismatch")
                fallback_contexts = provider_contexts_by_profile_id
                if fallback_contexts is None and isinstance(
                    provider_context,
                    Mapping,
                ):
                    nested = provider_context.get("provider_contexts_by_profile_id")
                    fallback_contexts = nested if isinstance(nested, Mapping) else None
                if fallback_contexts is not None and not isinstance(fallback_contexts, Mapping):
                    raise ProviderInvocationBlocked("provider_fallback_bindings_invalid")
                raw_candidate = (
                    fallback_contexts.get(profile_id) if fallback_contexts is not None and profile_id else None
                )
                if raw_candidate is None:
                    raise ProviderInvocationBlocked("provider_fallback_binding_required")
                candidate = ProviderInvocationContext.from_value(raw_candidate)
                candidate.assert_valid()
                cls._assert_same_provider_delegation(primary, candidate)
                if primary.require_hub_provider_budget and not candidate.require_hub_provider_budget:
                    raise ProviderInvocationBlocked("provider_fallback_binding_budget_mismatch")
                if primary.require_hub_provider_budget and candidate.provider_binding_id == primary.provider_binding_id:
                    raise ProviderInvocationBlocked("provider_fallback_binding_not_distinct")
            if candidate.selected_provider_id and (
                candidate.selected_provider_id != provider or candidate.selected_model_id != model
            ):
                raise ProviderInvocationBlocked("provider_selection_binding_mismatch")
            if candidate.require_hub_provider_attempt_budget and candidate.provider_profile_id != profile_id:
                raise ProviderInvocationBlocked("provider_attempt_plan_profile_mismatch")
            retry_attempt = max(
                int(primary.retry_attempt),
                int(candidate.retry_attempt),
            ) + max(0, int(request_attempt))
            retry_prefix = str(candidate.retry_id or primary.retry_id or "").strip() or (
                f"model-invocation:{candidate.run_id}:{candidate.attempt_id or 'unbound'}"
            )
            return candidate.for_attempt(
                retry_attempt,
                retry_id=f"{retry_prefix}:provider:{retry_attempt}",
            ).for_provider_call(f"provider-call:{uuid.uuid4().hex}")
        except ProviderInvocationBlocked as exc:
            raise cls._routing_policy_blocked_error(exc.reason_code) from exc
        except (TypeError, ValueError) as exc:
            raise cls._routing_policy_blocked_error("provider_context_invalid") from exc

    @staticmethod
    def _assert_same_provider_delegation(primary: Any, fallback: Any) -> None:
        from ananta_contracts.provider_invocation import ProviderInvocationBlocked

        binding_fields = (
            "tenant_id",
            "run_id",
            "workflow_id",
            "step_id",
            "plan_hash",
            "attempt_id",
            "fencing_token",
            "policy_version",
            "prompt_version",
        )
        if any(getattr(primary, field) != getattr(fallback, field) for field in binding_fields):
            raise ProviderInvocationBlocked("provider_fallback_binding_scope_mismatch")

    @staticmethod
    def _provider_attempt_plan(raw: Any) -> tuple[Any, ...]:
        if raw is None or raw == ():
            return ()
        if isinstance(raw, (str, bytes)) or not isinstance(
            raw,
            (list, tuple),
        ):
            raise ValueError("provider_attempt_plan_invalid")
        if not 1 <= len(raw) <= 8:
            raise ValueError("provider_attempt_plan_invalid")
        from ananta_contracts.provider_execution import (
            ProviderProfileAttemptPlanEntry,
        )

        values = tuple(
            item
            if isinstance(item, ProviderProfileAttemptPlanEntry)
            else ProviderProfileAttemptPlanEntry.from_mapping(item)
            for item in raw
        )
        if len({item.profile_id for item in values}) != len(values):
            raise ValueError("provider_attempt_plan_duplicate")
        return values

    @classmethod
    def _validated_provider_attempt_plan(
        cls,
        raw: Any,
    ) -> tuple[Any, ...]:
        try:
            return cls._provider_attempt_plan(raw)
        except (TypeError, ValueError) as exc:
            raise cls._routing_policy_blocked_error("provider_attempt_plan_invalid") from exc

    @staticmethod
    def _profiles_for_signed_attempt_plan(
        resolver: Any,
        signed_attempt_plan: tuple[Any, ...],
    ) -> tuple[list[Any], dict[str, Any]]:
        profiles: list[Any] = []
        for entry in signed_attempt_plan:
            profile = resolver.profile_by_id(entry.profile_id)
            if (
                profile is None
                or str(profile.provider_id).strip().lower() != entry.provider_id
                or str(profile.model).strip() != entry.model_id
            ):
                raise ModelRoutingConfigurationError("provider_attempt_plan_local_profile_mismatch")
            if entry.endpoint_identity:
                try:
                    endpoint_identity = normalize_provider_endpoint_identity(
                        provider_id=profile.provider_id,
                        endpoint_url=profile.base_url,
                    )
                except ValueError as exc:
                    raise ModelRoutingConfigurationError("provider_attempt_plan_local_endpoint_mismatch") from exc
                if endpoint_identity != entry.endpoint_identity:
                    raise ModelRoutingConfigurationError("provider_attempt_plan_local_endpoint_mismatch")
            profiles.append(profile)
        return profiles, {
            "profile_id": signed_attempt_plan[0].profile_id,
            "initial_profile_id": signed_attempt_plan[0].profile_id,
            "resolution_source": "hub_signed_provider_attempt_plan",
            "resolution_rank": 0,
            "candidate_chain": [entry.profile_id for entry in signed_attempt_plan],
            "profile_attempt_caps": {entry.profile_id: entry.maximum_attempts for entry in signed_attempt_plan},
        }

    @staticmethod
    def _signed_attempt_failure_action(
        *,
        signed_attempt_plan: tuple[Any, ...],
        index: int,
        failed_attempts: int,
        error_type: str,
        fallback_policy: Any,
        fallback_decisions: list[dict[str, Any]],
        call_profile: list[dict[str, Any]],
        error: LLMUnavailableError,
    ) -> str:
        current = signed_attempt_plan[index]
        normalized_error_type = fallback_policy.normalize_error_type(error_type)
        allowed_error_types = tuple(
            str(value or "").strip()
            for value in getattr(current, "allowed_error_types", ())
            if str(value or "").strip()
        )
        if allowed_error_types and normalized_error_type not in allowed_error_types:
            fallback_decisions.append(
                {
                    "reason": "hub_signed_fallback_trigger_denied",
                    "previous_profile_id": current.profile_id,
                    "next_profile_id": None,
                    "trigger": normalized_error_type,
                    "failed_attempts": failed_attempts,
                    "maximum_attempts": current.maximum_attempts,
                    "terminal": True,
                }
            )
            raise LLMUnavailableError(
                str(error),
                llm_call_profile=call_profile,
                fallback_decisions=fallback_decisions,
                terminal_reason=normalized_error_type,
            )
        if normalized_error_type == "context_too_large":
            fallback_decisions.append(
                {
                    "reason": ("hub_signed_context_recovery_required"),
                    "previous_profile_id": current.profile_id,
                    "next_profile_id": None,
                    "trigger": normalized_error_type,
                    "failed_attempts": failed_attempts,
                    "maximum_attempts": current.maximum_attempts,
                    "terminal": True,
                }
            )
            raise LLMUnavailableError(
                str(error),
                llm_call_profile=call_profile,
                fallback_decisions=fallback_decisions,
                terminal_reason=normalized_error_type,
            )
        if not fallback_policy.allows_fallback(normalized_error_type):
            fallback_decisions.append(
                {
                    "reason": (f"hub_signed_fallback_trigger_denied:{normalized_error_type}"),
                    "previous_profile_id": current.profile_id,
                    "next_profile_id": None,
                    "trigger": normalized_error_type,
                    "failed_attempts": failed_attempts,
                    "maximum_attempts": current.maximum_attempts,
                    "terminal": True,
                }
            )
            raise LLMUnavailableError(
                str(error),
                llm_call_profile=call_profile,
                fallback_decisions=fallback_decisions,
                terminal_reason=error_type,
            )
        if (
            fallback_policy.allows_same_profile_retry(normalized_error_type)
            and failed_attempts < current.maximum_attempts
        ):
            fallback_decisions.append(
                {
                    "reason": "hub_signed_same_profile_retry",
                    "previous_profile_id": current.profile_id,
                    "next_profile_id": current.profile_id,
                    "trigger": normalized_error_type,
                    "failed_attempts": failed_attempts,
                    "maximum_attempts": current.maximum_attempts,
                    "terminal": False,
                }
            )
            return "retry"
        if index + 1 < len(signed_attempt_plan):
            fallback_decisions.append(
                {
                    "reason": "hub_signed_profile_cap_exhausted",
                    "previous_profile_id": current.profile_id,
                    "next_profile_id": signed_attempt_plan[index + 1].profile_id,
                    "trigger": normalized_error_type,
                    "failed_attempts": failed_attempts,
                    "maximum_attempts": current.maximum_attempts,
                    "terminal": False,
                }
            )
            return "fallback"
        raise LLMUnavailableError(
            str(error),
            llm_call_profile=call_profile,
            fallback_decisions=fallback_decisions,
            terminal_reason=error_type,
        )
