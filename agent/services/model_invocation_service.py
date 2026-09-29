"""ModelInvocationService — real LLM HTTP calls for propose strategies. FA-T021.

The service is composed from single-responsibility mixins:

- ``model_invocation_support``: call-profile/error helpers and observation hooks
- ``model_invocation_routing_policy``: Hub-bound provider contexts and attempt plans
- ``model_invocation_provider_endpoints``: provider/profile to endpoint URL resolution
- ``model_invocation_response_contract``: tool-call and JSON-schema response validation
- ``model_invocation_provider_codec``: provider request bodies and response normalization
- ``model_invocation_chat_transport``: one provider HTTP attempt
- ``model_invocation_chat_pipeline``: candidate resolution, retry and fallback chain

This module keeps profile-resolver loading (its cache is module state that
callers reset), the provider middleware accessor and the public ``invoke*`` API.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import requests

from agent.services.model_invocation_chat_pipeline import (
    ModelInvocationChatPipelineMixin,
)
from agent.services.model_invocation_chat_transport import (
    _LMSTUDIO_INFERENCE_LOCK,
    ModelInvocationChatTransportMixin,
)
from agent.services.model_invocation_errors import (
    LLMUnavailableError,
    ModelRoutingConfigurationError,
)
from agent.services.model_invocation_provider_codec import (
    ModelInvocationProviderCodecMixin,
)
from agent.services.model_invocation_provider_endpoints import (
    ModelInvocationProviderEndpointMixin,
)
from agent.services.model_invocation_response_contract import (
    ModelInvocationResponseContractMixin,
)
from agent.services.model_invocation_routing_policy import (
    ModelInvocationRoutingPolicyMixin,
)
from agent.services.model_invocation_support import ModelInvocationSupportMixin

__all__ = [
    "LLMUnavailableError",
    "ModelInvocationService",
    "ModelRoutingConfigurationError",
    "_LMSTUDIO_INFERENCE_LOCK",
    "requests",
]

logger = logging.getLogger(__name__)

# Module-level resolver cache — loaded lazily, shared across calls.
_PROFILE_RESOLVER_CACHE: Any = None
_PROFILE_RESOLVER_LOCK = threading.Lock()


class ModelInvocationService(
    ModelInvocationChatPipelineMixin,
    ModelInvocationChatTransportMixin,
    ModelInvocationProviderCodecMixin,
    ModelInvocationResponseContractMixin,
    ModelInvocationProviderEndpointMixin,
    ModelInvocationRoutingPolicyMixin,
    ModelInvocationSupportMixin,
):
    """LLM invocation via OpenAI-compatible chat/completions endpoint."""

    _provider_middleware: Any = None

    @classmethod
    def _get_provider_middleware(cls):
        if cls._provider_middleware is None:
            from agent.services.provider_invocation_middleware import get_provider_invocation_middleware

            cls._provider_middleware = get_provider_invocation_middleware()
        return cls._provider_middleware

    @classmethod
    def _get_settings(cls):
        from agent.config import settings

        return settings

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
