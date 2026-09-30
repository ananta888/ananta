"""ModelInvocationService — real LLM HTTP calls for propose strategies. FA-T021.

The service composes single-responsibility collaborators, each built in
``__init__`` with its production default and replaceable through a
keyword-only constructor parameter:

- ``ModelProfileResolverLoader``: configured profile resolver (cached per loader)
- ``ModelInvocationRoutingPolicy``: Hub-bound provider contexts and attempt plans
- ``ProviderEndpointResolver``: provider/profile to endpoint URL resolution
- ``ResponseContractValidator``: tool-call and JSON-schema response validation
- ``ProviderWireCodec``: provider request bodies and response normalization
- ``ChatTransport``: one provider HTTP attempt
- ``ChatCallPipeline``: candidate resolution, retry and fallback chain
- ``ModelInvocationAttemptObserver``: content-free attempt observation

New callers receive a ``ModelInvocationService`` instance through their
constructor (production default: ``default_instance()``, the documented
process-wide composition-root instance). Class-level calls
(``ModelInvocationService.invoke``) remain a deprecated compatibility adapter
served by that same default instance. As of the second migration step no
production caller in ``agent/``, ``worker/`` or ``scripts/`` calls through the
class any more; the adapter stays because out-of-tree callers and tests still
access the class attributes (e.g. ``patch("...ModelInvocationService.invoke")``
or ``isinstance(ModelInvocationService.invoke, Mock)`` probes). Remove it only
once no class-level access remains.
"""

from __future__ import annotations

import functools
import json
import threading
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

import requests

from agent.services.model_invocation_chat_pipeline import (
    ChatCallPipeline,
    ChatCallRunning,
    ProviderEndpointPort,
    RoutingPolicyPort,
)
from agent.services.model_invocation_chat_transport import (
    _LMSTUDIO_INFERENCE_LOCK,
    ChatTransport,
    ProviderWireCoding,
    SingleChatCallTransport,
    default_provider_middleware,
    post_with_requests,
)
from agent.services.model_invocation_errors import (
    LLMUnavailableError,
    ModelRoutingConfigurationError,
)
from agent.services.model_invocation_payload_helpers import normalize_openai_tools
from agent.services.model_invocation_profile_resolver_loader import (
    ModelProfileResolverLoader,
)
from agent.services.model_invocation_provider_codec import ProviderWireCodec
from agent.services.model_invocation_provider_endpoints import (
    ProviderEndpointResolver,
    resolve_runtime_handoff_endpoint,
)
from agent.services.model_invocation_response_contract import (
    ResponseContractValidating,
    ResponseContractValidator,
)
from agent.services.model_invocation_routing_policy import (
    ModelInvocationRoutingPolicy,
)
from agent.services.model_invocation_support import (
    InvocationAttemptObserving,
    ModelInvocationAttemptObserver,
    current_invocation_cancelled,
)

__all__ = [
    "LLMUnavailableError",
    "ModelInvocationService",
    "ModelRoutingConfigurationError",
    "_LMSTUDIO_INFERENCE_LOCK",
    "requests",
]


def _application_settings() -> Any:
    from agent.config import settings

    return settings


class _CachedProvider:
    """Resolve an expensive dependency once, on first use."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._value: Any = None
        self._lock = threading.Lock()

    def __call__(self) -> Any:
        if self._value is None:
            with self._lock:
                if self._value is None:
                    self._value = self._factory()
        return self._value


class _shared_default_method:  # noqa: N801 -- used as a decorator
    """Bind a method to its instance, or on class access to the shared default instance.

    Keeps the historical class-level API (``ModelInvocationService.invoke``)
    while the behavior lives on composed instances.
    """

    def __init__(self, function: Callable[..., Any]) -> None:
        self._function = function
        functools.update_wrapper(self, function)

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            if owner is None:
                raise TypeError("shared default method needs an owner class")
            instance = owner.default_instance()
        return self._function.__get__(instance, owner)


class ModelInvocationService:
    """LLM invocation via OpenAI-compatible chat/completions endpoint."""

    _default_instance: ClassVar[ModelInvocationService | None] = None
    _default_instance_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        *,
        settings_provider: Callable[[], Any] | None = None,
        profile_resolver_provider: Callable[[], Any] | None = None,
        provider_middleware_provider: Callable[[], Any] | None = None,
        http_post: Callable[..., Any] | None = None,
        cancellation_probe: Callable[[], bool] | None = None,
        attempt_observer: InvocationAttemptObserving | None = None,
        routing_policy: RoutingPolicyPort | None = None,
        endpoint_resolver: ProviderEndpointPort | None = None,
        response_contract: ResponseContractValidating | None = None,
        wire_codec: ProviderWireCoding | None = None,
        chat_transport: SingleChatCallTransport | None = None,
        chat_pipeline: ChatCallRunning | None = None,
    ) -> None:
        self._settings = settings_provider or _application_settings
        self._profile_resolver = profile_resolver_provider or ModelProfileResolverLoader()
        routing: RoutingPolicyPort = routing_policy or ModelInvocationRoutingPolicy()
        self._endpoints: ProviderEndpointPort = endpoint_resolver or ProviderEndpointResolver(
            settings_provider=self._settings
        )
        self._contract: ResponseContractValidating = response_contract or ResponseContractValidator()
        self._transport: SingleChatCallTransport = chat_transport or ChatTransport(
            codec=wire_codec or ProviderWireCodec(),
            contract=self._contract,
            middleware_provider=provider_middleware_provider or _CachedProvider(default_provider_middleware),
            http_post=http_post or post_with_requests,
            cancellation_probe=cancellation_probe or current_invocation_cancelled,
        )
        self._pipeline: ChatCallRunning = chat_pipeline or ChatCallPipeline(
            resolver_provider=self._profile_resolver,
            settings_provider=self._settings,
            routing_policy=routing,
            endpoints=self._endpoints,
            transport=self._transport,
            observer=attempt_observer or ModelInvocationAttemptObserver(),
        )

    @classmethod
    def default_instance(cls) -> ModelInvocationService:
        """The process-wide instance that serves class-level calls."""
        instance = cls._default_instance
        if instance is None:
            with cls._default_instance_lock:
                instance = cls._default_instance
                if instance is None:
                    instance = cls()
                    cls._default_instance = instance
        return instance

    @_shared_default_method
    def get_profile_resolver(self):
        """Lazily load ModelProfileResolver from the configured profiles path.
        Returns None only when no model-routing configuration was requested."""
        return self._profile_resolver()

    @_shared_default_method
    def _get_resolver(self):
        """Compatibility alias for callers predating the public resolver accessor."""

        return self._profile_resolver()

    @_shared_default_method
    def get_context_recovery_policy(self) -> dict[str, Any]:
        """Return the Hub-loaded, non-executable recovery policy.

        The resolver remains the source of truth for the configured routing
        file.  Returning only the two allowlisted recovery fields keeps this
        read model separate from invocation and task orchestration.
        """
        resolver = self._profile_resolver()
        rules = getattr(resolver, "rules", None) if resolver is not None else None
        if rules is None:
            return {}
        return {
            "context_recovery_strategies": list(getattr(rules, "context_recovery_strategies", []) or []),
            "require_approval_for_generated_plan": bool(getattr(rules, "require_approval_for_generated_plan", True)),
        }

    @staticmethod
    def resolve_runtime_handoff_endpoint(
        *,
        tenant_id: str,
        endpoint_id: str,
        required_capability: str,
        expected_endpoint_revision: int | None = None,
        endpoint_registry: Any | None = None,
    ) -> Mapping[str, Any]:
        """Resolve one explicit endpoint revision; never select a fallback."""
        return resolve_runtime_handoff_endpoint(
            tenant_id=tenant_id,
            endpoint_id=endpoint_id,
            required_capability=required_capability,
            expected_endpoint_revision=expected_endpoint_revision,
            endpoint_registry=endpoint_registry,
        )

    @_shared_default_method
    def _make_chat_call(
        self,
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
        """Compatibility entry for callers that drive the attempt chain directly."""
        return self._pipeline.make_chat_call(
            messages,
            tools=tools,
            response_format=response_format,
            response_validator=response_validator,
            model=model,
            timeout=timeout,
            routing_ctx=routing_ctx,
            provider_context=provider_context,
            provider_contexts_by_profile_id=provider_contexts_by_profile_id,
            provider_attempt_plan=provider_attempt_plan,
        )

    @_shared_default_method
    def _make_single_chat_call(
        self,
        messages: list[dict],
        *,
        tools: list | None,
        response_format: dict | None,
        response_validator: Callable[[dict[str, Any]], None] | None = None,
        attempt: dict[str, Any],
        resolution_info: dict[str, Any],
        provider_context: Any = None,
    ) -> dict:
        """Compatibility entry for callers that run exactly one provider attempt."""
        return self._transport.make_single_chat_call(
            messages,
            tools=tools,
            response_format=response_format,
            response_validator=response_validator,
            attempt=attempt,
            resolution_info=resolution_info,
            provider_context=provider_context,
        )

    @_shared_default_method
    def _provider_info_from_profile(self, profile) -> tuple[str, str, str | None]:
        """Compatibility entry: (provider_label, url, api_key) for a ModelProfile."""
        return self._endpoints.provider_info_from_profile(profile)

    @_shared_default_method
    def invoke_with_tools(self, prompt: str, tools: list, model: str | None = None, **kwargs) -> dict:
        """Call LLM with tools= parameter. Returns dict with tool_calls list and content."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages

        response = self._pipeline.make_chat_call(
            messages,
            tools=tools,
            response_validator=(
                lambda payload: self._contract.validate_tool_response(payload, tools)
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
                for item in normalize_openai_tools(tools)
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
            "provider": str(final_call.get("provider") or "").strip() or self._endpoints.provider_info()[0],
            "model": response.get("model") or final_call.get("model") or model,
        }

    @_shared_default_method
    def invoke_with_json_schema(self, prompt: str, json_schema: dict, model: str | None = None, **kwargs) -> str:
        """Call LLM with response_format=json_object. Returns raw content string."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = self._pipeline.make_chat_call(
            messages,
            response_format={"type": "json_object"},
            response_validator=(
                lambda payload: self._contract.validate_json_schema_response(
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

    @_shared_default_method
    def invoke_with_json_schema_result(
        self, prompt: str, json_schema: dict, model: str | None = None, **kwargs
    ) -> dict[str, Any]:
        """Call LLM with response_format=json_object, metadata and strict validation."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = self._pipeline.make_chat_call(
            messages,
            response_format={"type": "json_object"},
            response_validator=(
                lambda payload: self._contract.validate_json_schema_response(
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
        provider = str(final_call.get("provider") or "").strip() or self._endpoints.provider_info()[0]
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

    @_shared_default_method
    def invoke(self, prompt: str, model: str | None = None, **kwargs) -> str:
        """Plain chat completion. Returns content string."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = self._pipeline.make_chat_call(
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

    @_shared_default_method
    def invoke_result(self, prompt: str, model: str | None = None, **kwargs) -> dict[str, Any]:
        """Plain chat completion with metadata/usage (additive API)."""
        messages = [{"role": "user", "content": prompt}]
        if kwargs.get("system_prompt"):
            messages = [{"role": "system", "content": kwargs["system_prompt"]}] + messages
        response = self._pipeline.make_chat_call(
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
        provider = str(final_call.get("provider") or "").strip() or self._endpoints.provider_info()[0]
        return {
            "content": (msg.get("content") or "") if isinstance(msg, dict) else "",
            "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
            "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
            "metadata": metadata,
            "provider": provider,
            "model": response.get("model") or final_call.get("model") or model,
        }
