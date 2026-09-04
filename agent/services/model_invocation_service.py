"""ModelInvocationService — real LLM HTTP calls for propose strategies. FA-T021."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import requests

from agent.services.local_runtime_request_policy import LocalRuntimeRequestPolicy
from agent.services.local_runtime_response_adapters import (
    LocalRuntimeResponseError,
    normalize_ollama_chat,
    normalize_ollama_generate,
)
from agent.services.model_invocation_errors import (
    LLMUnavailableError,
    ModelRoutingConfigurationError,
)
from agent.services.model_invocation_observation_helpers import (
    observe_model_invocation_attempt,
)
from agent.services.model_invocation_payload_helpers import (
    blocked_candidates_as_dict,
    fallback_error_type,
    finalize_trace_error,
    max_output_tokens_for_request,
    messages_for_tool_mode,
    normalize_openai_tools,
    response_message,
    tool_calling_mode,
)
from agent.services.model_invocation_profile import (
    build_llm_call_profile_entry,
)
from agent.services.model_invocation_response_policy import (
    ResponsePolicyFailureProjector,
    apply_local_response_policy,
)
from ananta_contracts.provider_endpoint_policy import (
    build_provider_request_url,
    normalize_provider_endpoint_identity,
)

logger = logging.getLogger(__name__)

# LM Studio handles one inference at a time. Concurrent requests return empty content
# because the second request is queued/dropped. This lock serializes all LM Studio calls
# across threads (Flask runs with threaded=True, so planning and propose can overlap).
_LMSTUDIO_INFERENCE_LOCK = threading.Lock()

# Module-level resolver cache — loaded lazily, shared across calls.
_PROFILE_RESOLVER_CACHE: Any = None
_PROFILE_RESOLVER_LOCK = threading.Lock()


class ModelInvocationService:
    """LLM invocation via OpenAI-compatible chat/completions endpoint."""

    _provider_middleware: Any = None

    @classmethod
    def _get_provider_middleware(cls):
        if cls._provider_middleware is None:
            from agent.services.provider_invocation_middleware import get_provider_invocation_middleware

            cls._provider_middleware = get_provider_invocation_middleware()
        return cls._provider_middleware

    _build_llm_call_profile_entry = staticmethod(build_llm_call_profile_entry)

    _observe_model_invocation_attempt = staticmethod(observe_model_invocation_attempt)

    @classmethod
    def _observe_successful_model_invocation_attempt(
        cls,
        *,
        payload: Any,
        attempt: Mapping[str, Any],
        resolution_info: Mapping[str, Any],
    ) -> None:
        profiles = (
            payload.get("metadata", {}).get("llm_call_profile", [])
            if isinstance(payload, dict) and isinstance(payload.get("metadata"), dict)
            else []
        )
        cls._observe_model_invocation_attempt(
            attempt=attempt,
            resolution_info=resolution_info,
            success=True,
            reason_code="invocation_completed",
            call_profile=(profiles[-1] if profiles and isinstance(profiles[-1], dict) else None),
        )

    @classmethod
    def _observe_failed_model_invocation_attempt(
        cls,
        *,
        error: LLMUnavailableError,
        error_type: str,
        attempt: Mapping[str, Any],
        resolution_info: Mapping[str, Any],
    ) -> None:
        profiles = error.llm_call_profile or []
        cls._observe_model_invocation_attempt(
            attempt=attempt,
            resolution_info=resolution_info,
            success=False,
            reason_code=error_type,
            call_profile=(profiles[-1] if profiles and isinstance(profiles[-1], dict) else None),
        )

    @staticmethod
    def _decorate_invocation_payload(
        payload: Any,
        *,
        call_profile: list[dict[str, Any]],
        fallback_decisions: list[dict[str, Any]],
        resolution_info: Mapping[str, Any],
    ) -> Any:
        if not isinstance(payload, dict):
            return payload
        metadata = payload.get("metadata")
        meta = dict(metadata) if isinstance(metadata, dict) else {}
        meta["llm_call_profile"] = call_profile + list(meta.get("llm_call_profile") or [])
        meta["fallback_decisions"] = list(fallback_decisions)
        if resolution_info:
            meta["resolution_info"] = dict(resolution_info)
        payload["metadata"] = meta
        return payload

    @classmethod
    def _raise_llm_error(
        cls,
        *,
        message: str,
        name: str,
        backend: str,
        provider: str | None,
        model: str | None,
        started_at: float | None,
        error_type: str,
    ) -> None:
        ended_at = time.time()
        entry = cls._build_llm_call_profile_entry(
            name=name,
            backend=backend,
            provider=provider,
            model=model,
            success=False,
            started_at=started_at,
            ended_at=ended_at,
            usage=None,
            source="model_invocation_service",
            estimated=False,
            error_type=error_type,
            error_message=message,
        )
        raise LLMUnavailableError(
            message,
            llm_call_profile=[entry],
            terminal_reason=error_type,
        )

    @staticmethod
    def _current_invocation_cancelled() -> bool:
        """Read the existing Hub/Worker request fence without owning it."""

        try:
            from agent.services.lmstudio_request_registry import (
                _get_current_context,
                is_cancelled,
            )

            return is_cancelled(*_get_current_context())
        except Exception:
            return False

    @staticmethod
    def _provider_response_too_large(response: Any, *, maximum_bytes: int = 2 * 1024 * 1024) -> bool:
        headers = getattr(response, "headers", None)
        if isinstance(headers, Mapping):
            declared = headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > maximum_bytes:
                        return True
                except (TypeError, ValueError):
                    return True
        content = getattr(response, "content", None)
        return isinstance(content, (bytes, bytearray)) and len(content) > maximum_bytes

    @staticmethod
    def _validate_local_runtime_payload(*, provider: str, payload: Mapping[str, Any]) -> None:
        if provider in {"ollama", "lmstudio", "lm_studio"}:
            LocalRuntimeRequestPolicy().validate_payload(payload)

    @classmethod
    def _enforce_provider_response_limit(
        cls,
        *,
        response: Any,
        middleware: Any,
        prepared: Any,
        provider: str,
        model: str,
        prompt_trace: Any,
        trace_service: Any,
        started_at: float,
    ) -> None:
        if not cls._provider_response_too_large(response):
            return
        middleware.fail(
            prepared,
            provider=provider,
            model=model,
            reason_code="provider_response_too_large",
        )
        cls._finalize_trace_error(
            prompt_trace,
            trace_service,
            "provider_response_too_large",
            "provider_response_too_large",
        )
        cls._raise_llm_error(
            message="llm_provider_response_too_large",
            name="chat_completions",
            backend="llm_api",
            provider=provider,
            model=model,
            started_at=started_at,
            error_type="provider_response_too_large",
        )

    @classmethod
    def _get_settings(cls):
        from agent.config import settings

        return settings

    @classmethod
    def resolve_runtime_handoff_endpoint(
        cls,
        *,
        tenant_id: str,
        endpoint_id: str,
        required_capability: str,
        expected_endpoint_revision: int | None = None,
        endpoint_registry: Any | None = None,
    ) -> Mapping[str, Any]:
        """Resolve one explicit endpoint revision; never select a fallback."""

        if endpoint_registry is None:
            from flask import current_app

            from agent.services.unsloth_runtime_handoff_composition import (
                runtime_endpoint_registry_from_config,
            )

            endpoint_registry = runtime_endpoint_registry_from_config(
                dict(current_app.config.get("AGENT_CONFIG", {}) or {})
            )
        return endpoint_registry.resolve_for_invocation(
            tenant_id=tenant_id,
            endpoint_id=endpoint_id,
            required_capability=required_capability,
            expected_revision=expected_endpoint_revision,
        )

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
        return ModelInvocationService._routing_policy_blocked_error("configured_model_routing_unavailable")

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

    @classmethod
    def get_profile_resolver(cls):
        """Lazily load ModelProfileResolver from the configured profiles path.
        Returns None only when no model-routing configuration was requested."""
        global _PROFILE_RESOLVER_CACHE
        if _PROFILE_RESOLVER_CACHE is not None:
            return _PROFILE_RESOLVER_CACHE
        with _PROFILE_RESOLVER_LOCK:
            if _PROFILE_RESOLVER_CACHE is not None:
                return _PROFILE_RESOLVER_CACHE
            try:
                import os
                from pathlib import Path

                from agent.services.model_master_default_service import get_global_master_default_service
                from agent.services.model_profile_loader import ModelProfileLoader
                from agent.services.model_profile_resolver import (
                    ModelProfileResolver,
                    RoutingRules,
                    SecurityPolicyChecker,
                )

                profiles_path_env = os.environ.get("MODEL_PROFILES_PATH", "").strip()
                routing_path_str = (
                    os.environ.get("MODEL_ROUTING_PATH", "").strip()
                    or os.environ.get("ANANTA_MODEL_ROUTING_PATH", "").strip()
                )
                if not profiles_path_env:
                    if routing_path_str:
                        raise ModelRoutingConfigurationError("model_profiles_path_required_for_configured_routing")
                    return None
                path = Path(profiles_path_env)
                if not path.exists():
                    raise ModelRoutingConfigurationError("configured_model_profiles_file_not_found")
                result = ModelProfileLoader().load_file(path)
                if not result.ok or not result.profiles:
                    logger.warning("model_invocation: profile load errors: %s", result.errors)
                    raise ModelRoutingConfigurationError("configured_model_profiles_invalid")

                logger.info("model_invocation: loaded %d profiles from %s", len(result.profiles), path)

                # Load routing rules
                routing_rules = RoutingRules()
                if routing_path_str:
                    rp = Path(routing_path_str)
                    if not rp.exists():
                        raise ModelRoutingConfigurationError("configured_model_routing_file_not_found")
                    try:
                        from jsonschema import Draft202012Validator

                        raw_routing = json.loads(rp.read_text(encoding="utf-8"))
                        if not isinstance(raw_routing, dict):
                            raise ValueError("model_routing_root_must_be_object")
                        schema_path = (
                            Path(__file__).resolve().parents[2] / "config" / "schemas" / "model_routing.schema.json"
                        )
                        schema = json.loads(schema_path.read_text(encoding="utf-8"))
                        Draft202012Validator(schema).validate(raw_routing)
                        routing_rules = RoutingRules.from_dict(
                            raw_routing,
                            strict=True,
                        )
                        logger.info("model_invocation: loaded routing rules from %s", rp)
                    except Exception as exc:
                        logger.warning(
                            "model_invocation: configured routing load failed for %s: %s",
                            rp,
                            exc,
                        )
                        raise ModelRoutingConfigurationError("configured_model_routing_invalid") from exc
                else:
                    logger.debug("model_invocation: no MODEL_ROUTING_PATH set — using empty rules")

                # Load global master default
                master_svc = get_global_master_default_service()
                master_profile = master_svc.get_master_profile()

                style_ranking = None
                try:
                    from agent.services.cognitive_style_service import (
                        get_cognitive_style_ranking_policy,
                    )

                    style_ranking = get_cognitive_style_ranking_policy(
                        weight=float(os.environ.get("COGNITIVE_STYLE_ROUTING_WEIGHT", ".25"))
                    )
                except Exception as exc:
                    logger.warning(
                        "model_invocation: cognitive style ranking unavailable: %s",
                        type(exc).__name__,
                    )

                resolver = ModelProfileResolver(
                    profiles=result.profiles,
                    security_policy=SecurityPolicyChecker(),
                    routing_rules=routing_rules,
                    master_default_profile=master_profile,
                    style_ranking=style_ranking,
                )
                _PROFILE_RESOLVER_CACHE = resolver

                if master_profile:
                    logger.info(
                        "model_invocation: global master default active: provider=%s model=%s",
                        master_profile.provider_id,
                        master_profile.model,
                    )

                # AMR-020: log deprecation warning if legacy env vars are still set
                import os as _os

                if _os.environ.get("DEFAULT_PROVIDER") or _os.environ.get("DEFAULT_MODEL"):
                    logger.warning(
                        "model_invocation: DEFAULT_PROVIDER/DEFAULT_MODEL env vars are set but "
                        "MODEL_PROFILES_PATH is also configured. Profile-based routing takes "
                        "precedence. Remove DEFAULT_PROVIDER/DEFAULT_MODEL to silence this warning."
                    )
                return resolver
            except ModelRoutingConfigurationError:
                raise
            except Exception as exc:
                logger.warning("model_invocation: resolver init failed: %s", exc)
                if cls._model_routing_configuration_requested():
                    raise ModelRoutingConfigurationError("configured_model_routing_initialization_failed") from exc
                return None

    @classmethod
    def _get_resolver(cls):
        """Compatibility alias for callers predating the public resolver accessor."""

        return cls.get_profile_resolver()

    @classmethod
    def get_context_recovery_policy(cls) -> dict[str, Any]:
        """Return the Hub-loaded, non-executable recovery policy.

        The resolver remains the source of truth for the configured routing
        file.  Returning only the two allowlisted recovery fields keeps this
        read model separate from invocation and task orchestration.
        """
        resolver = cls._get_resolver()
        rules = getattr(resolver, "rules", None) if resolver is not None else None
        if rules is None:
            return {}
        return {
            "context_recovery_strategies": list(getattr(rules, "context_recovery_strategies", []) or []),
            "require_approval_for_generated_plan": bool(getattr(rules, "require_approval_for_generated_plan", True)),
        }

    @classmethod
    def _provider_info_from_profile(cls, profile) -> tuple[str, str, str | None]:
        """Convert a ModelProfile to (provider_label, url, api_key)."""
        import os

        s = cls._get_settings()
        provider = profile.provider_id.lower()
        base_url = (profile.base_url or "").rstrip("/")
        api_key: str | None = None

        if profile.api_key_env:
            api_key = os.environ.get(profile.api_key_env) or None

        if not base_url:
            if provider in ("lmstudio", "lm_studio"):
                base_url = s.lmstudio_url.rstrip("/")
            elif provider == "ollama":
                base_url = s.ollama_url.rstrip("/")
                if "/api/generate" in base_url:
                    base_url = base_url.replace("/api/generate", "")
                if not base_url.endswith("/v1"):
                    base_url = base_url + "/v1"
            elif provider == "openai":
                base_url = "https://api.openai.com/v1"
                if not api_key:
                    api_key = s.openai_api_key
            elif provider == "openrouter":
                base_url = "https://openrouter.ai/api/v1"
            elif provider == "mock":
                base_url = s.mock_url.rstrip("/") + "/v1"

        ollama_native = provider == "ollama" and base_url.endswith("/api/generate")
        if (
            not ollama_native
            and provider == "ollama"
            and "/chat/completions" not in base_url
            and not base_url.endswith("/v1")
        ):
            base_url = base_url + "/v1"
        if not ollama_native and not base_url.endswith("/chat/completions"):
            if not base_url.endswith("/v1"):
                # already has path like /v1/chat/completions — leave as-is if it has /chat
                if "/chat" not in base_url:
                    base_url = base_url + "/chat/completions"
                # else trust the URL
            else:
                base_url = base_url + "/chat/completions"

        return (
            provider,
            build_provider_request_url(
                provider_id=provider,
                endpoint_url=base_url,
            ),
            api_key,
        )

    @classmethod
    def _provider_info(cls) -> tuple[str, str, str | None]:
        """Return (provider_label, chat_completions_url, api_key)."""
        s = cls._get_settings()
        provider = (s.default_provider or "lmstudio").strip().lower()

        if provider in ("lmstudio", "lm_studio"):
            base = s.lmstudio_url.rstrip("/")
            # lmstudio_url may point to /v1 base or /v1/chat/completions
            if not base.endswith("/chat/completions"):
                base = base + "/chat/completions"
            return "lmstudio", base, None

        if provider == "ollama":
            base = s.ollama_url.rstrip("/")
            # ollama_url defaults to /api/generate; use OpenAI-compat endpoint
            if "/api/generate" in base:
                base = base.replace("/api/generate", "")
            if not base.endswith("/chat/completions"):
                if not base.endswith("/v1"):
                    base = base + "/v1"
                base = base + "/chat/completions"
            return "ollama", base, None

        if provider == "openai":
            url = s.openai_url.rstrip("/")
            if not url.endswith("/chat/completions"):
                url = url + "/chat/completions"
            return "openai", url, s.openai_api_key

        if provider == "mock":
            return "mock", s.mock_url.rstrip("/") + "/v1/chat/completions", None

        # Generic OpenAI-compatible fallback
        base = s.lmstudio_url.rstrip("/")
        if not base.endswith("/chat/completions"):
            base = base + "/chat/completions"
        return provider, base, None

    _normalize_openai_tools = staticmethod(normalize_openai_tools)
    _tool_calling_mode = staticmethod(tool_calling_mode)
    _max_output_tokens_for_request = staticmethod(max_output_tokens_for_request)
    _messages_for_tool_mode = staticmethod(messages_for_tool_mode)
    _blocked_candidates_as_dict = staticmethod(blocked_candidates_as_dict)
    _fallback_error_type = staticmethod(fallback_error_type)
    _finalize_trace_error = staticmethod(finalize_trace_error)
    _response_message = staticmethod(response_message)

    @classmethod
    def _raise_response_contract_error(
        cls,
        payload: dict[str, Any],
        *,
        error_type: str,
        detail: str,
    ) -> None:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        profile = [dict(item) for item in list(metadata.get("llm_call_profile") or []) if isinstance(item, dict)]
        if profile:
            profile[-1] = {
                **profile[-1],
                "success": False,
                "error_type": error_type,
                "error_message": str(detail or error_type)[:200],
            }
        else:
            profile.append(
                cls._build_llm_call_profile_entry(
                    name="chat_completions",
                    backend="response_validation",
                    provider=None,
                    model=None,
                    success=False,
                    started_at=None,
                    ended_at=None,
                    error_type=error_type,
                    error_message=str(detail or error_type)[:200],
                )
            )
        raise LLMUnavailableError(
            f"llm_{error_type}: {str(detail or error_type)[:200]}",
            llm_call_profile=profile,
            terminal_reason=error_type,
        )

    @classmethod
    def _validate_tool_response(cls, payload: dict[str, Any], tools: list | None) -> None:
        normalized_tools = cls._normalize_openai_tools(tools)
        allowed_tools = {
            item["function"]["name"]: item["function"].get("parameters") or {"type": "object", "properties": {}}
            for item in normalized_tools
            if isinstance(item.get("function"), dict)
        }
        if not allowed_tools:
            return

        _, message = cls._response_message(payload)
        native_calls = message.get("tool_calls")
        if isinstance(native_calls, list) and native_calls:
            for raw_call in native_calls:
                if not isinstance(raw_call, dict):
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_call_must_be_object",
                    )
                function = raw_call.get("function")
                if not isinstance(function, dict):
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_call_function_missing",
                    )
                tool_name = str(function.get("name") or "").strip()
                if tool_name not in allowed_tools:
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_not_allowed",
                        detail="tool_name_not_in_request_contract",
                    )
                raw_args = function.get("arguments", {})
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except (TypeError, ValueError):
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_arguments_are_not_valid_json",
                    )
                if not isinstance(args, dict):
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_arguments_must_be_object",
                    )
                try:
                    import jsonschema

                    jsonschema.validate(instance=args, schema=allowed_tools[tool_name])
                except ImportError:
                    pass
                except Exception:
                    cls._raise_response_contract_error(
                        payload,
                        error_type="tool_args_invalid",
                        detail="tool_arguments_failed_schema_validation",
                    )
            return

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        call_profile = [item for item in list(metadata.get("llm_call_profile") or []) if isinstance(item, dict)]
        tool_mode = str((call_profile[-1] if call_profile else {}).get("tool_calling_mode") or "").strip()
        # Native-tool profiles may intentionally answer with structured content,
        # which the strategy normalizer still handles for compatibility.
        if tool_mode not in {"prompt_json", "both"}:
            return

        try:
            selection = json.loads(str(message.get("content") or ""))
        except (TypeError, ValueError):
            cls._raise_response_contract_error(
                payload,
                error_type="tool_args_invalid",
                detail="prompt_json_tool_selection_is_not_valid_json",
            )
        if not isinstance(selection, dict):
            cls._raise_response_contract_error(
                payload,
                error_type="tool_args_invalid",
                detail="prompt_json_tool_selection_must_be_object",
            )
        tool_name = str(selection.get("tool") or "").strip()
        if tool_name not in allowed_tools:
            cls._raise_response_contract_error(
                payload,
                error_type="tool_not_allowed",
                detail="prompt_json_tool_name_not_in_request_contract",
            )
        args = selection.get("args")
        if not isinstance(args, dict):
            cls._raise_response_contract_error(
                payload,
                error_type="tool_args_invalid",
                detail="prompt_json_tool_args_must_be_object",
            )
        try:
            import jsonschema

            jsonschema.validate(instance=args, schema=allowed_tools[tool_name])
        except ImportError:
            pass
        except Exception:
            cls._raise_response_contract_error(
                payload,
                error_type="tool_args_invalid",
                detail="prompt_json_tool_args_failed_schema_validation",
            )

    @classmethod
    def _validate_json_schema_response(
        cls,
        payload: dict[str, Any],
        *,
        json_schema: dict[str, Any],
        allow_format_repair: bool,
    ) -> None:
        _, message = cls._response_message(payload)
        from agent.services.structured_output_service import StructuredOutputService

        structured = StructuredOutputService(max_repair_attempts=1 if allow_format_repair else 0).validate_json(
            str(message.get("content") or ""),
            json_schema,
            allow_format_repair=allow_format_repair,
        )
        if structured.valid:
            return
        issue_codes = [
            str((issue.as_dict() if hasattr(issue, "as_dict") else {}).get("reason_code") or "").strip()
            for issue in list(structured.issues or [])[:4]
        ]
        detail = "schema_validation_failed"
        normalized_codes = [code for code in issue_codes if code]
        if normalized_codes:
            detail = f"{detail}:{','.join(normalized_codes)}"
        cls._raise_response_contract_error(
            payload,
            error_type="schema_validation_failed",
            detail=detail,
        )

    @staticmethod
    def _provider_response_redirect_denied(
        *,
        provider: str,
        request_url: str,
        response: Any,
    ) -> bool:
        if 300 <= int(response.status_code) < 400:
            return True
        response_url = str(getattr(response, "url", "") or "").strip()
        if not response_url:
            return False
        try:
            return normalize_provider_endpoint_identity(
                provider_id=provider,
                endpoint_url=response_url,
            ) != normalize_provider_endpoint_identity(
                provider_id=provider,
                endpoint_url=request_url,
            )
        except ValueError:
            return True

    @staticmethod
    def _ollama_generate_request_body(
        *,
        messages: list[dict],
        model: str,
        profile: Any,
        provider_context: Any,
        tools_requested: bool,
    ) -> dict[str, Any]:
        if tools_requested:
            raise LLMUnavailableError(
                "ollama_generate_native_tools_unsupported",
                terminal_reason="policy_blocked",
            )
        system_prompt = "\n".join(
            str(message.get("content") or "")
            for message in messages
            if (isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "system")
        ).strip()
        prompt = "\n".join(
            f"{str(message.get('role') or 'user')}: {str(message.get('content') or '')}"
            for message in messages
            if (isinstance(message, dict) and str(message.get("role") or "").strip().lower() != "system")
        )
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            body["system"] = system_prompt
        if profile is not None:
            body["options"] = {
                "temperature": float(profile.temperature),
                "num_predict": (
                    ModelInvocationService._max_output_tokens_for_request(
                        profile,
                        provider_context,
                    )
                ),
            }
        return body

    @staticmethod
    def _normalize_ollama_generate_response(
        payload: Any,
        *,
        model: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        try:
            normalized = normalize_ollama_generate(payload)
        except LocalRuntimeResponseError:
            return {}
        prompt_tokens = int(normalized["usage"]["prompt_tokens"] or 0)
        completion_tokens = int(normalized["usage"]["completion_tokens"] or 0)
        return {
            "choices": [
                {
                    "message": {
                        "content": normalized["content"],
                        "tool_calls": [],
                        "reasoning_content": normalized["thinking"],
                    },
                    "finish_reason": normalized["finish_reason"] or (
                        "stop" if normalized["done"] else None
                    ),
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "model": str(payload.get("model") or model),
        }

    @staticmethod
    def _normalize_ollama_chat_response(
        payload: Any,
        *,
        model: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        try:
            normalized = normalize_ollama_chat(payload)
        except LocalRuntimeResponseError:
            return {}
        prompt_tokens = int(normalized["usage"]["prompt_tokens"] or 0)
        completion_tokens = int(normalized["usage"]["completion_tokens"] or 0)
        tool_calls = [
            {
                "id": item["id"],
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": json.dumps(
                        item["arguments"],
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            }
            for item in normalized["tool_calls"]
        ]
        return {
            "choices": [
                {
                    "message": {
                        "content": normalized["content"],
                        "tool_calls": tool_calls,
                        "reasoning_content": normalized["thinking"],
                    },
                    "finish_reason": normalized["finish_reason"] or (
                        "stop" if normalized["done"] else None
                    ),
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "model": str(payload.get("model") or model),
        }

    @classmethod
    def _provider_request_body(
        cls,
        *,
        provider: str,
        url: str,
        model: str,
        messages: list[dict],
        profile: Any,
        provider_context: Any,
        tools: list | None,
        send_native_tools: bool,
        response_format: dict | None,
    ) -> tuple[dict[str, Any], bool]:
        from agent.services.model_prompt_prefix_service import (
            ModelPromptPrefixService,
        )

        effective_messages = ModelPromptPrefixService.apply(
            messages,
            profile=profile,
        )
        ollama_generate = provider == "ollama" and str(url).endswith("/api/generate")
        if ollama_generate:
            return (
                cls._ollama_generate_request_body(
                    messages=effective_messages,
                    model=model,
                    profile=profile,
                    provider_context=provider_context,
                    tools_requested=bool(tools and send_native_tools),
                ),
                True,
            )
        body: dict[str, Any] = {
            "model": model,
            "messages": effective_messages,
        }
        if profile is not None:
            body["temperature"] = float(profile.temperature)
            body["max_tokens"] = cls._max_output_tokens_for_request(
                profile,
                provider_context,
            )
        if tools and send_native_tools:
            body["tools"] = cls._normalize_openai_tools(tools)
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format
        return body, False

    @classmethod
    def _normalize_provider_response(
        cls,
        payload: Any,
        *,
        ollama_generate: bool,
        ollama_chat: bool,
        model: str,
    ) -> Any:
        if ollama_generate:
            return cls._normalize_ollama_generate_response(
                payload,
                model=model,
            )
        if ollama_chat:
            return cls._normalize_ollama_chat_response(
                payload,
                model=model,
            )
        return payload

    @classmethod
    def _make_single_chat_call(
        cls,
        messages: list[dict],
        *,
        tools: list | None,
        response_format: dict | None,
        response_validator: Callable[[dict[str, Any]], None] | None = None,
        attempt: dict[str, Any],
        resolution_info: dict[str, Any],
        provider_context: Any = None,
    ) -> dict:
        provider = attempt["provider"]
        url = attempt["url"]
        api_key = attempt.get("api_key")
        effective_model = attempt["model"]
        timeout = int(attempt.get("timeout") or 120)
        profile = attempt.get("profile")
        tool_mode = cls._tool_calling_mode(profile)
        outgoing_messages, send_native_tools = cls._messages_for_tool_mode(
            messages,
            tools=tools,
            tool_calling_mode=tool_mode,
        )
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body, ollama_generate = cls._provider_request_body(
            provider=provider,
            url=url,
            model=effective_model,
            messages=outgoing_messages,
            profile=profile,
            provider_context=provider_context,
            tools=tools,
            send_native_tools=send_native_tools,
            response_format=response_format,
        )
        cls._validate_local_runtime_payload(provider=provider, payload=body)

        if cls._current_invocation_cancelled():
            cls._raise_llm_error(
                message="llm_invocation_cancelled",
                name="chat_completions",
                backend="request_cancellation_fence",
                provider=provider,
                model=effective_model,
                started_at=time.time(),
                error_type="cancelled",
            )

        middleware = cls._get_provider_middleware()
        try:
            prepared = middleware.prepare(
                context=provider_context,
                provider=provider,
                model=effective_model,
                endpoint_url=url,
                payload=body,
            )
        except Exception as exc:
            from agent.services.provider_invocation_middleware import ProviderInvocationBlocked

            if not isinstance(exc, ProviderInvocationBlocked):
                raise
            cls._raise_llm_error(
                message=exc.reason_code,
                name="chat_completions",
                backend="provider_middleware",
                provider=provider,
                model=effective_model,
                started_at=time.time(),
                error_type=exc.reason_code,
            )
        body = prepared.payload
        if prepared.cached_response is not None:
            if cls._current_invocation_cancelled():
                cls._raise_llm_error(
                    message="llm_invocation_cancelled",
                    name="chat_completions",
                    backend="request_cancellation_fence",
                    provider=provider,
                    model=effective_model,
                    started_at=time.time(),
                    error_type="cancelled",
                )
            cached_payload = dict(prepared.cached_response)
            cached_meta = (
                dict(cached_payload.get("metadata")) if isinstance(cached_payload.get("metadata"), dict) else {}
            )
            cached_meta["provider_middleware"] = {
                "schema": "ananta.provider_middleware_result.v1",
                "cache_key": prepared.cache_key,
                "payload_hash": prepared.payload_hash,
                "cache_hit": True,
            }
            cached_payload["metadata"] = cached_meta
            cached_payload = apply_local_response_policy(
                cached_payload,
                profile=profile,
                tools_requested=bool(tools),
                raise_contract_error=cls._raise_response_contract_error,
            )
            if response_validator is not None:
                response_validator(cached_payload)
            return cached_payload

        prompt_trace = None
        trace_svc = None
        try:
            from flask import g, has_app_context

            if has_app_context():
                from agent.services.prompt_trace_service import get_prompt_trace_service

                trace_goal_id = str(getattr(g, "llm_goal_id", "") or "").strip() or None
                trace_task_id = str(getattr(g, "llm_task_id", "") or "").strip() or None
                trace_svc = get_prompt_trace_service()
                prompt_trace = trace_svc.create_trace(
                    goal_id=trace_goal_id,
                    task_id=trace_task_id,
                    source_component="model_invocation_service",
                    provider=provider,
                    transport_provider=provider,
                    model=effective_model,
                    endpoint_kind="chat_completions",
                    request_kind="propose",
                    messages=[message for message in list(body.get("messages") or []) if isinstance(message, dict)],
                    tools=(list(body.get("tools") or []) if send_native_tools else []),
                    llm_scope="task",
                    sensitivity_level="internal",
                )
        except Exception:
            prompt_trace = None
            trace_svc = None

        started_at = time.time()
        lock = _LMSTUDIO_INFERENCE_LOCK if provider in ("lmstudio", "lm_studio") else None
        if lock is not None:
            if not lock.acquire(blocking=False):
                logger.debug("LM Studio busy - waiting for inference lock (provider=%s)", provider)
                lock.acquire()
        try:
            try:
                resp = requests.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=False,
                )
            except requests.exceptions.ConnectionError:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="connection_error",
                )
                cls._finalize_trace_error(prompt_trace, trace_svc, "connection_error", "provider_connection_failed")
                cls._raise_llm_error(
                    message="llm_connection_failed",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="connection_error",
                )
            except requests.exceptions.Timeout:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="timeout",
                )
                cls._finalize_trace_error(prompt_trace, trace_svc, "timeout", "provider_timeout")
                cls._raise_llm_error(
                    message="llm_timeout",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="timeout",
                )

            if cls._current_invocation_cancelled():
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="cancelled",
                )
                cls._finalize_trace_error(
                    prompt_trace,
                    trace_svc,
                    "cancelled",
                    "llm_invocation_cancelled",
                )
                cls._raise_llm_error(
                    message="llm_invocation_cancelled",
                    name="chat_completions",
                    backend="request_cancellation_fence",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="cancelled",
                )

            if cls._provider_response_redirect_denied(
                provider=provider,
                request_url=url,
                response=resp,
            ):
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="provider_redirect_denied",
                )
                cls._finalize_trace_error(
                    prompt_trace,
                    trace_svc,
                    "provider_redirect_denied",
                    f"HTTP {resp.status_code}",
                )
                cls._raise_llm_error(
                    message=(f"llm_provider_redirect_denied: HTTP {resp.status_code}"),
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="provider_redirect_denied",
                )
            cls._enforce_provider_response_limit(
                response=resp,
                middleware=middleware,
                prepared=prepared,
                provider=provider,
                model=effective_model,
                prompt_trace=prompt_trace,
                trace_service=trace_svc,
                started_at=started_at,
            )
            if resp.status_code >= 500:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="server_error",
                )
                cls._finalize_trace_error(prompt_trace, trace_svc, "server_error", f"HTTP {resp.status_code}")
                cls._raise_llm_error(
                    message=f"llm_server_error: HTTP {resp.status_code}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="server_error",
                )
            if resp.status_code >= 400:
                response_excerpt = str(resp.text or "")[:200]
                normalized_response = response_excerpt.lower()
                error_type = (
                    "context_too_large"
                    if any(
                        marker in normalized_response
                        for marker in (
                            "context length",
                            "context window",
                            "too many tokens",
                            "maximum context",
                            "num_ctx",
                        )
                    )
                    else "client_error"
                )
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code=error_type,
                )
                cls._finalize_trace_error(
                    prompt_trace, trace_svc, error_type, f"HTTP {resp.status_code}"
                )
                cls._raise_llm_error(
                    message=f"llm_{error_type}: HTTP {resp.status_code}",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type=error_type,
                )

            try:
                payload = resp.json()
            except Exception:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="invalid_json_response",
                )
                cls._finalize_trace_error(
                    prompt_trace,
                    trace_svc,
                    "invalid_json_response",
                    "invalid_json_response",
                )
                cls._raise_llm_error(
                    message="llm_invalid_json_response",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="invalid_json_response",
                )
            payload = cls._normalize_provider_response(
                payload,
                ollama_generate=ollama_generate,
                ollama_chat=(
                    provider == "ollama"
                    and str(url).rstrip("/").endswith("/api/chat")
                ),
                model=effective_model,
            )
            payload = apply_local_response_policy(
                payload if isinstance(payload, dict) else {},
                profile=profile,
                tools_requested=bool(tools),
                on_failure=ResponsePolicyFailureProjector(
                    middleware=middleware,
                    prepared=prepared,
                    provider=provider,
                    model=effective_model,
                    prompt_trace=prompt_trace,
                    trace_service=trace_svc,
                    finalize_trace_error=cls._finalize_trace_error,
                ),
                raise_contract_error=cls._raise_response_contract_error,
            )

            first_choice = (payload.get("choices") or [{}])[0] if isinstance(payload, dict) else {}
            first_message = first_choice.get("message") if isinstance(first_choice, dict) else {}
            has_content = bool(str((first_message or {}).get("content") or "").strip())
            has_tool_calls = bool((first_message or {}).get("tool_calls"))
            if not has_content and not has_tool_calls:
                middleware.fail(
                    prepared,
                    provider=provider,
                    model=effective_model,
                    reason_code="empty_content",
                )
                cls._finalize_trace_error(prompt_trace, trace_svc, "empty_content", "LLM response has no content")
                cls._raise_llm_error(
                    message="llm_empty_content",
                    name="chat_completions",
                    backend="llm_api",
                    provider=provider,
                    model=effective_model,
                    started_at=started_at,
                    error_type="empty_content",
                )

            ended_at = time.time()
            usage = payload.get("usage") if isinstance(payload, dict) else {}
            call_entry = cls._build_llm_call_profile_entry(
                name="chat_completions",
                backend="llm_api",
                provider=provider,
                model=effective_model,
                success=True,
                started_at=started_at,
                ended_at=ended_at,
                usage=usage if isinstance(usage, dict) else None,
            )
            call_entry["profile_id"] = getattr(profile, "profile_id", None)
            call_entry["tool_calling_mode"] = tool_mode
            if isinstance(payload, dict):
                meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                if prompt_trace is not None:
                    meta["prompt_trace_id"] = str(getattr(prompt_trace, "trace_id", "") or "")
                meta["llm_call_profile"] = list(meta.get("llm_call_profile") or []) + [call_entry]
                if resolution_info:
                    meta["resolution_info"] = dict(resolution_info)
                payload["metadata"] = meta
            if response_validator is not None:
                try:
                    response_validator(payload)
                except LLMUnavailableError as exc:
                    error_type = cls._fallback_error_type(exc)
                    middleware.fail(
                        prepared,
                        provider=provider,
                        model=effective_model,
                        reason_code=error_type,
                    )
                    cls._finalize_trace_error(
                        prompt_trace,
                        trace_svc,
                        error_type,
                        str(exc),
                    )
                    raise
            middleware_result = middleware.complete(
                prepared,
                provider=provider,
                model=effective_model,
                response=payload if isinstance(payload, dict) else {},
            )
            if prompt_trace is not None and trace_svc is not None:
                try:
                    msg_content = ""
                    if isinstance(payload, dict):
                        first = (payload.get("choices") or [{}])[0] or {}
                        msg = first.get("message") if isinstance(first, dict) else {}
                        msg_content = str((msg or {}).get("content") or "")
                    finalized = trace_svc.finalize_trace(
                        prompt_trace,
                        success=True,
                        response_text=msg_content or None,
                        usage=usage if isinstance(usage, dict) else None,
                    )
                    trace_svc.store(finalized)
                except Exception:
                    pass
            if isinstance(payload, dict):
                meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                meta["provider_middleware"] = middleware_result
                payload["metadata"] = meta
            return payload
        finally:
            if lock is not None:
                lock.release()

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

    @classmethod
    def invoke_with_tools(cls, prompt: str, tools: list, model: str | None = None, **kwargs) -> dict:
        """Call LLM with tools= parameter. Returns dict with tool_calls list and content."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages

        response = cls._make_chat_call(
            messages,
            tools=tools,
            response_validator=(
                lambda payload: cls._validate_tool_response(payload, tools)
                if bool(kwargs.get("retry_on_contract_error", False))
                else None
            ),
            model=model,
            timeout=kwargs.get("timeout"),
            routing_ctx=kwargs.get("routing_ctx"),
            provider_context=kwargs.get("provider_context"),
            provider_contexts_by_profile_id=kwargs.get("provider_contexts_by_profile_id"),
            provider_attempt_plan=kwargs.get("provider_attempt_plan"),
        )
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") or {}

        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments", "{}")
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                parsed_args = {"raw": raw_args}
            tool_calls.append(
                {
                    "name": fn.get("name", ""),
                    "args": parsed_args,
                    "id": tc.get("id"),
                }
            )
        if not tool_calls and msg.get("content"):
            allowed_tools = {
                item["function"]["name"]: item["function"].get("parameters") or {"type": "object", "properties": {}}
                for item in cls._normalize_openai_tools(tools)
                if isinstance(item.get("function"), dict)
            }
            try:
                prompt_json_call = json.loads(msg.get("content") or "{}")
            except Exception:
                prompt_json_call = None
            if isinstance(prompt_json_call, dict):
                tool_name = str(prompt_json_call.get("tool") or "").strip()
                args = prompt_json_call.get("args")
                args_valid = False
                if tool_name in allowed_tools and isinstance(args, dict):
                    try:
                        import jsonschema

                        jsonschema.validate(instance=args, schema=allowed_tools[tool_name])
                        args_valid = True
                    except ImportError:
                        args_valid = True
                    except Exception:
                        args_valid = False
                if args_valid:
                    tool_calls.append(
                        {
                            "name": tool_name,
                            "args": args,
                            "id": prompt_json_call.get("id") or f"prompt-json-{len(tool_calls) + 1}",
                            "confidence": prompt_json_call.get("confidence"),
                            "reasoning_summary": prompt_json_call.get("reasoning_summary"),
                        }
                    )

        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        call_profile = [item for item in list(metadata.get("llm_call_profile") or []) if isinstance(item, dict)]
        final_call = call_profile[-1] if call_profile else {}
        return {
            "tool_calls": tool_calls,
            "content": msg.get("content") or "",
            "finish_reason": choice.get("finish_reason"),
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": str(final_call.get("provider") or "").strip() or cls._provider_info()[0],
            "model": response.get("model") or final_call.get("model") or model,
        }

    @classmethod
    def invoke_with_json_schema(cls, prompt: str, json_schema: dict, model: str | None = None, **kwargs) -> str:
        """Call LLM with response_format=json_object. Returns raw content string."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages,
            response_format={"type": "json_object"},
            response_validator=(
                lambda payload: cls._validate_json_schema_response(
                    payload,
                    json_schema=json_schema,
                    allow_format_repair=bool(kwargs.get("allow_format_repair", False)),
                )
                if bool(kwargs.get("retry_on_contract_error", False))
                else None
            ),
            model=model,
            timeout=kwargs.get("timeout"),
            routing_ctx=kwargs.get("routing_ctx"),
            provider_context=kwargs.get("provider_context"),
            provider_contexts_by_profile_id=kwargs.get("provider_contexts_by_profile_id"),
            provider_attempt_plan=kwargs.get("provider_attempt_plan"),
        )
        choice = (response.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""

    @classmethod
    def invoke_with_json_schema_result(
        cls, prompt: str, json_schema: dict, model: str | None = None, **kwargs
    ) -> dict[str, Any]:
        """Call LLM with response_format=json_object, metadata and strict validation."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages,
            response_format={"type": "json_object"},
            response_validator=(
                lambda payload: cls._validate_json_schema_response(
                    payload,
                    json_schema=json_schema,
                    allow_format_repair=bool(kwargs.get("allow_format_repair", False)),
                )
                if bool(kwargs.get("retry_on_contract_error", False))
                else None
            ),
            model=model,
            timeout=kwargs.get("timeout"),
            routing_ctx=kwargs.get("routing_ctx"),
            provider_context=kwargs.get("provider_context"),
            provider_contexts_by_profile_id=kwargs.get("provider_contexts_by_profile_id"),
            provider_attempt_plan=kwargs.get("provider_attempt_plan"),
        )
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") if isinstance(choice, dict) else {}
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        call_profile = [item for item in list(metadata.get("llm_call_profile") or []) if isinstance(item, dict)]
        final_call = call_profile[-1] if call_profile else {}
        provider = str(final_call.get("provider") or "").strip() or cls._provider_info()[0]
        content = (msg.get("content") or "") if isinstance(msg, dict) else ""
        from agent.services.structured_output_service import StructuredOutputService

        structured = StructuredOutputService(
            max_repair_attempts=1 if bool(kwargs.get("allow_format_repair", False)) else 0
        ).validate_json(
            content,
            json_schema,
            allow_format_repair=bool(kwargs.get("allow_format_repair", False)),
        )
        return {
            "content": content,
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": provider,
            "model": response.get("model") or final_call.get("model") or model,
            "structured_output": structured.value,
            "structured_output_valid": structured.valid,
            "structured_output_issues": [issue.as_dict() for issue in structured.issues],
            "structured_output_audit": [dict(item) for item in structured.audit_events],
        }

    @classmethod
    def invoke(cls, prompt: str, model: str | None = None, **kwargs) -> str:
        """Plain chat completion. Returns content string."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages,
            model=model,
            timeout=kwargs.get("timeout"),
            routing_ctx=kwargs.get("routing_ctx"),
            provider_context=kwargs.get("provider_context"),
            provider_contexts_by_profile_id=kwargs.get("provider_contexts_by_profile_id"),
            provider_attempt_plan=kwargs.get("provider_attempt_plan"),
        )
        choice = (response.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""

    @classmethod
    def invoke_result(cls, prompt: str, model: str | None = None, **kwargs) -> dict[str, Any]:
        """Plain chat completion with metadata/usage (additive API)."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = cls._make_chat_call(
            messages,
            model=model,
            timeout=kwargs.get("timeout"),
            routing_ctx=kwargs.get("routing_ctx"),
            provider_context=kwargs.get("provider_context"),
            provider_contexts_by_profile_id=kwargs.get("provider_contexts_by_profile_id"),
            provider_attempt_plan=kwargs.get("provider_attempt_plan"),
        )
        choice = (response.get("choices") or [{}])[0]
        msg = choice.get("message") if isinstance(choice, dict) else {}
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        call_profile = [item for item in list(metadata.get("llm_call_profile") or []) if isinstance(item, dict)]
        final_call = call_profile[-1] if call_profile else {}
        provider = str(final_call.get("provider") or "").strip() or cls._provider_info()[0]
        return {
            "content": (msg.get("content") or "") if isinstance(msg, dict) else "",
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": provider,
            "model": response.get("model") or final_call.get("model") or model,
        }
