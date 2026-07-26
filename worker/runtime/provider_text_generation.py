"""Worker-local, Hub-budgeted text generation for delegated workflows."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol

from ananta_contracts.provider_endpoint_policy import (
    LOCAL_PROVIDER_IDS,
    build_provider_request_url,
    is_forbidden_provider_endpoint_target,
    is_legacy_compatible_provider_endpoint,
    is_local_provider_endpoint,
    normalize_provider_endpoint_identity,
    validate_provider_endpoint_resolution,
)
from ananta_contracts.provider_invocation import (
    ProviderBudgetDecision,
    ProviderInvocationBlocked,
    ProviderInvocationContext,
)
from ananta_contracts.redaction import VisibilityLevel, redact
from worker.runtime.workflow_hub_gateway import (
    HttpWorkflowHubDecisionClient,
    HubProviderBudgetAdapter,
)

_MAX_PROMPT_CHARS = 1_048_576
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_LOCAL_PROVIDER_IDS = LOCAL_PROVIDER_IDS


class WorkerProviderTransportPort(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]: ...


class WorkerProviderBudgetPort(Protocol):
    def reserve(
        self,
        *,
        context: ProviderInvocationContext,
        estimated_prompt_tokens: int,
        reservation_id: str = "",
    ) -> ProviderBudgetDecision: ...

    def reconcile(
        self,
        *,
        context: ProviderInvocationContext,
        reserved_tokens: int,
        actual_total_tokens: int | None,
        reservation_id: str = "",
    ) -> None: ...


class WorkerModelRoutingPort(Protocol):
    def invoke_result(
        self,
        prompt: str,
        model: str | None = None,
        **values: Any,
    ) -> dict[str, Any]: ...


class WorkerProviderHttpTransport:
    """Bounded JSON transport with no provider-selection responsibility."""

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(
                _DenyRedirectHandler()
            )
            with opener.open(request, timeout=timeout_seconds) as response:
                if str(response.geturl() or "") != url:
                    raise ProviderInvocationBlocked(
                        "worker_provider_redirect_denied"
                    )
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderInvocationBlocked("worker_provider_timeout") from exc
        except urllib.error.HTTPError as exc:
            raise ProviderInvocationBlocked("worker_provider_http_error") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderInvocationBlocked("worker_provider_unavailable") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ProviderInvocationBlocked("worker_provider_response_too_large")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderInvocationBlocked("worker_provider_response_invalid") from exc
        if not isinstance(decoded, dict):
            raise ProviderInvocationBlocked("worker_provider_response_invalid")
        return decoded


class _DenyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the Hub-signed provider origin and path immutable."""

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        del req, fp, code, msg, headers, newurl
        return None


class HubBudgetedWorkerTextGeneration:
    """Invoke only the exact Hub-selected provider under a Hub reservation."""

    def __init__(
        self,
        *,
        provider_urls: Mapping[str, object],
        budgets: WorkerProviderBudgetPort,
        transport: WorkerProviderTransportPort | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if budgets is None:
            raise ValueError("worker_provider_hub_budget_required")
        self._provider_urls = {
            str(key).strip().lower(): str(value or "").strip()
            for key, value in provider_urls.items()
            if str(key).strip()
        }
        self._budgets = budgets
        self._transport = transport or WorkerProviderHttpTransport()
        self._environment = os.environ if environment is None else environment

    def generate_text(self, **values: Any) -> dict[str, Any]:
        context = ProviderInvocationContext.from_value(values.get("provider_context"))
        context.assert_valid()
        if not context.require_hub_provider_budget:
            raise ProviderInvocationBlocked("worker_provider_hub_budget_required")
        provider = str(values.get("provider") or "").strip().lower()
        model = str(values.get("model") or "").strip()
        if (
            not provider
            or not model
            or provider != context.selected_provider_id.lower()
            or model != context.selected_model_id
        ):
            raise ProviderInvocationBlocked("provider_selection_binding_mismatch")

        endpoint = self._endpoint(provider)
        self._assert_endpoint_authorized(
            context=context,
            provider=provider,
            endpoint=endpoint,
        )
        prompt = str(values.get("prompt") or "")
        if not prompt or len(prompt) > _MAX_PROMPT_CHARS:
            raise ProviderInvocationBlocked("worker_provider_prompt_invalid")
        safe_prompt = str(redact(prompt, VisibilityLevel.PUBLIC))
        estimated_prompt_tokens = max(1, (len(safe_prompt) + 3) // 4)
        if context.max_total_tokens < 1 or context.max_completion_tokens_per_call < 1:
            raise ProviderInvocationBlocked("worker_provider_token_budget_required")
        completion_limit = min(
            context.max_completion_tokens_per_call,
            max(0, context.max_total_tokens - estimated_prompt_tokens),
        )
        if completion_limit < 1:
            raise ProviderInvocationBlocked("provider_token_budget_exceeded")
        context = replace(
            context,
            max_completion_tokens_per_call=completion_limit,
        )
        if not context.provider_call_id:
            context = context.for_provider_call(
                f"provider-call:{uuid.uuid4().hex}"
            )
        timeout_seconds = max(1.0, min(float(values.get("timeout") or 60), 120.0))
        request_url, headers, payload = self._request(
            provider=provider,
            model=model,
            endpoint=endpoint,
            prompt=safe_prompt,
            max_output_tokens=context.max_completion_tokens_per_call,
        )
        reservation_id = self._reservation_id(context, provider, model, safe_prompt)
        decision = self._budgets.reserve(
            context=context,
            estimated_prompt_tokens=estimated_prompt_tokens,
            reservation_id=reservation_id,
        )
        if not decision.allowed:
            raise ProviderInvocationBlocked(
                decision.reason_code or "worker_provider_hub_budget_denied"
            )
        response = self._transport.post_json(
            url=request_url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        text, usage = self._response(provider, response)
        self._budgets.reconcile(
            context=context,
            reserved_tokens=decision.reserved_tokens,
            actual_total_tokens=self._total_tokens(usage),
            reservation_id=reservation_id,
        )
        return {
            "text": text,
            "usage": usage,
            "provider": provider,
            "model": model,
        }

    @staticmethod
    def _assert_endpoint_authorized(
        *,
        context: ProviderInvocationContext,
        provider: str,
        endpoint: str,
    ) -> None:
        try:
            actual_identity = normalize_provider_endpoint_identity(
                provider_id=provider,
                endpoint_url=endpoint,
            )
        except ValueError as exc:
            raise ProviderInvocationBlocked(
                "provider_endpoint_identity_invalid"
            ) from exc
        if is_forbidden_provider_endpoint_target(actual_identity):
            raise ProviderInvocationBlocked(
                "provider_endpoint_target_denied"
            )
        expected_identity = context.provider_endpoint_identity
        endpoint_bound = bool(expected_identity)
        if endpoint_bound and actual_identity != expected_identity:
            raise ProviderInvocationBlocked(
                "provider_endpoint_binding_mismatch"
            )
        if (
            context.require_hub_provider_budget
            and not endpoint_bound
            and not is_legacy_compatible_provider_endpoint(
                provider_id=provider,
                endpoint_url=actual_identity,
            )
        ):
            raise ProviderInvocationBlocked(
                "provider_endpoint_binding_required"
            )
        if (
            not context.external_egress_allowed
            and not is_local_provider_endpoint(
                endpoint_url=actual_identity,
                provider_id=provider,
                endpoint_bound=endpoint_bound,
            )
        ):
            raise ProviderInvocationBlocked("provider_egress_denied")
        try:
            validate_provider_endpoint_resolution(
                provider_id=provider,
                endpoint_url=actual_identity,
                endpoint_bound=endpoint_bound,
            )
        except ValueError as exc:
            raise ProviderInvocationBlocked(
                str(exc) or "provider_endpoint_resolution_denied"
            ) from exc

    def _endpoint(self, provider: str) -> str:
        alias = "openai" if provider == "codex" else provider
        raw = self._provider_urls.get(provider) or self._provider_urls.get(alias) or ""
        parsed = urllib.parse.urlsplit(raw)
        if (
            not raw
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ProviderInvocationBlocked("worker_provider_endpoint_not_configured")
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )

    def _request(
        self,
        *,
        provider: str,
        model: str,
        endpoint: str,
        prompt: str,
        max_output_tokens: int,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        try:
            url = build_provider_request_url(
                provider_id=provider,
                endpoint_url=endpoint,
            )
        except ValueError as exc:
            raise ProviderInvocationBlocked(
                "worker_provider_endpoint_not_configured"
            ) from exc
        if provider == "ollama" and url.endswith("/api/generate"):
            payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
            if max_output_tokens > 0:
                payload["options"] = {"num_predict": max_output_tokens}
            return url, headers, payload
        if provider == "anthropic":
            api_key = str(self._environment.get("ANTHROPIC_API_KEY") or "").strip()
            if not api_key:
                raise ProviderInvocationBlocked("worker_provider_credential_missing")
            headers.update(
                {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                }
            )
            return url, headers, {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_output_tokens or 1024,
            }
        api_key = self._api_key(provider)
        if provider not in _LOCAL_PROVIDER_IDS and not api_key:
            raise ProviderInvocationBlocked("worker_provider_credential_missing")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if max_output_tokens > 0:
            payload["max_tokens"] = max_output_tokens
        return url, headers, payload

    def _api_key(self, provider: str) -> str:
        names = {
            "codex": "OPENAI_API_KEY",
            "gemini": "GOOGLE_API_KEY",
            "groq": "GROQ_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        name = names.get(provider)
        return str(self._environment.get(name, "") if name else "").strip()

    @staticmethod
    def _response(
        provider: str, response: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        usage = dict(response.get("usage") or {})
        if provider == "ollama" and "response" in response:
            text = str(response.get("response") or "")
            usage = {
                "prompt_tokens": int(response.get("prompt_eval_count") or 0),
                "completion_tokens": int(response.get("eval_count") or 0),
            }
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        elif provider == "anthropic":
            blocks = response.get("content") or ()
            text = "".join(
                str(item.get("text") or "")
                for item in blocks
                if isinstance(item, Mapping)
            )
            usage = {
                "prompt_tokens": int(usage.get("input_tokens") or 0),
                "completion_tokens": int(usage.get("output_tokens") or 0),
            }
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        else:
            choices = response.get("choices") or ()
            first = choices[0] if isinstance(choices, list) and choices else {}
            message = first.get("message") if isinstance(first, Mapping) else {}
            text = str(message.get("content") or "") if isinstance(message, Mapping) else ""
        if not text:
            raise ProviderInvocationBlocked("worker_provider_empty_response")
        return text[:_MAX_RESPONSE_BYTES], usage

    @staticmethod
    def _total_tokens(usage: Mapping[str, Any]) -> int | None:
        raw = usage.get("total_tokens")
        try:
            return max(0, int(raw)) if raw is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _reservation_id(
        context: ProviderInvocationContext,
        provider: str,
        model: str,
        prompt: str,
    ) -> str:
        source = ":".join(
            (
                context.tenant_id,
                context.run_id,
                context.step_id,
                context.attempt_id,
                provider,
                model,
                hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                context.provider_call_id,
            )
        )
        return "provider-call-" + hashlib.sha256(source.encode("utf-8")).hexdigest()


class HubProfileRoutedWorkerTextGeneration:
    """Use ModelInvocation only for a Hub-authorized profile binding set."""

    def __init__(
        self,
        *,
        direct: HubBudgetedWorkerTextGeneration,
        model_routing: WorkerModelRoutingPort | None = None,
    ) -> None:
        self._direct = direct
        self._model_routing = model_routing

    def generate_text(self, **values: Any) -> dict[str, Any]:
        raw_contexts = values.get("provider_contexts_by_profile_id")
        if raw_contexts is None or raw_contexts == {}:
            return self._direct.generate_text(**values)
        if not isinstance(raw_contexts, Mapping) or len(raw_contexts) > 8:
            raise ProviderInvocationBlocked("provider_profile_contexts_invalid")
        primary = ProviderInvocationContext.from_value(
            values.get("provider_context")
        )
        primary.assert_valid()
        provider = str(values.get("provider") or "").strip().lower()
        model = str(values.get("model") or "").strip()
        if (
            provider != primary.selected_provider_id.lower()
            or model != primary.selected_model_id
        ):
            raise ProviderInvocationBlocked("provider_selection_binding_mismatch")
        contexts: dict[str, dict[str, Any]] = {}
        parsed_contexts: dict[str, ProviderInvocationContext] = {}
        for raw_profile_id, raw_context in raw_contexts.items():
            profile_id = str(raw_profile_id or "").strip()
            if (
                not profile_id
                or len(profile_id) > 256
                or "\x00" in profile_id
                or not isinstance(raw_context, Mapping)
            ):
                raise ProviderInvocationBlocked(
                    "provider_profile_contexts_invalid"
                )
            candidate = ProviderInvocationContext.from_value(dict(raw_context))
            candidate.assert_valid()
            contexts[profile_id] = dict(raw_context)
            parsed_contexts[profile_id] = candidate
        from agent.services.workflow_runtime.security import (
            RuntimeAuthorizationEnvelope,
        )

        try:
            envelope = RuntimeAuthorizationEnvelope.from_mapping(
                primary.authorization_envelope
            )
        except Exception as exc:
            raise ProviderInvocationBlocked(
                "provider_authorization_invalid"
            ) from exc
        signed_plan = envelope.provider_attempt_plan
        if signed_plan:
            if (
                set(parsed_contexts)
                != {item.profile_id for item in signed_plan}
                or primary.max_attempts
                != sum(item.maximum_attempts for item in signed_plan)
            ):
                raise ProviderInvocationBlocked(
                    "provider_attempt_plan_context_mismatch"
                )
            for item in signed_plan:
                candidate = parsed_contexts[item.profile_id]
                if (
                    candidate.provider_profile_id != item.profile_id
                    or candidate.provider_binding_id != item.binding_id
                    or candidate.selected_provider_id != item.provider_id
                    or candidate.selected_model_id != item.model_id
                ):
                    raise ProviderInvocationBlocked(
                        "provider_attempt_plan_binding_mismatch"
                    )
            if parsed_contexts[signed_plan[0].profile_id] != primary:
                raise ProviderInvocationBlocked(
                    "provider_attempt_plan_primary_mismatch"
                )
        routing = self._model_routing
        if routing is None:
            from agent.services.model_invocation_service import (
                ModelInvocationService,
            )

            routing = ModelInvocationService
        routing_context = None
        raw_model_routing = values.get("model_routing")
        if raw_model_routing is not None:
            try:
                from agent.services.model_routing_contract import (
                    ModelRoutingConfig,
                    build_model_routing_context,
                )

                compiled = ModelRoutingConfig.assert_runtime_mapping(
                    raw_model_routing
                )
                routing_context = build_model_routing_context(
                    {"model_routing": compiled.as_metadata()},
                    context_text=str(values.get("prompt") or ""),
                )
            except Exception as exc:
                raise ProviderInvocationBlocked(
                    "model_routing_invalid"
                ) from exc
        result = routing.invoke_result(
            prompt=str(values.get("prompt") or ""),
            model=None,
            timeout=int(values.get("timeout") or 60),
            routing_ctx=routing_context,
            provider_context=values.get("provider_context"),
            provider_contexts_by_profile_id=contexts,
            provider_attempt_plan=[
                item.to_dict()
                for item in signed_plan
            ],
        )
        return {
            "text": str(result.get("content") or ""),
            "usage": (
                dict(result.get("usage") or {})
                if isinstance(result.get("usage"), Mapping)
                else {}
            ),
            "provider": str(result.get("provider") or ""),
            "model": str(result.get("model") or ""),
            "metadata": (
                dict(result.get("metadata") or {})
                if isinstance(result.get("metadata"), Mapping)
                else {}
            ),
        }


def build_hub_budgeted_worker_text_generation(
    *,
    client: HttpWorkflowHubDecisionClient,
    provider_urls: Mapping[str, object],
    transport: WorkerProviderTransportPort | None = None,
    model_routing: WorkerModelRoutingPort | None = None,
) -> HubProfileRoutedWorkerTextGeneration:
    """Production composition; the budget adapter always calls back to Hub."""

    if client is None:
        raise ValueError("worker_provider_hub_client_required")
    direct = HubBudgetedWorkerTextGeneration(
        provider_urls=provider_urls,
        budgets=HubProviderBudgetAdapter(client),
        transport=transport,
    )
    return HubProfileRoutedWorkerTextGeneration(
        direct=direct,
        model_routing=model_routing,
    )


__all__ = [
    "HubBudgetedWorkerTextGeneration",
    "HubProfileRoutedWorkerTextGeneration",
    "WorkerProviderBudgetPort",
    "WorkerProviderHttpTransport",
    "WorkerProviderTransportPort",
    "WorkerModelRoutingPort",
    "build_hub_budgeted_worker_text_generation",
]
