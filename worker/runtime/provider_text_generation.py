"""Worker-local, Hub-budgeted text generation for delegated workflows."""

from __future__ import annotations

import hashlib
import ipaddress
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
_LOCAL_PROVIDER_IDS = frozenset(
    {
        "koboldcpp",
        "llamacpp",
        "lmstudio",
        "local",
        "local_mock",
        "mock",
        "ollama",
        "openai_compatible",
        "textgen_webui",
    }
)


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
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
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
        if not context.external_egress_allowed and not self._is_private_endpoint(
            endpoint, provider=provider
        ):
            raise ProviderInvocationBlocked("provider_egress_denied")
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
        if provider == "ollama" and "/api/" in urllib.parse.urlsplit(endpoint).path:
            url = endpoint if endpoint.endswith("/api/generate") else endpoint + "/api/generate"
            payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
            if max_output_tokens > 0:
                payload["options"] = {"num_predict": max_output_tokens}
            return url, headers, payload
        if provider == "anthropic":
            url = endpoint if endpoint.endswith("/v1/messages") else endpoint + "/v1/messages"
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
        url = self._openai_chat_url(endpoint)
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
    def _openai_chat_url(endpoint: str) -> str:
        if endpoint.endswith("/chat/completions"):
            return endpoint
        if endpoint.endswith("/v1"):
            return endpoint + "/chat/completions"
        return endpoint + "/v1/chat/completions"

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
                uuid.uuid4().hex,
            )
        )
        return "provider-call-" + hashlib.sha256(source.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_private_endpoint(endpoint: str, *, provider: str) -> bool:
        if provider not in _LOCAL_PROVIDER_IDS:
            return False
        host = (urllib.parse.urlsplit(endpoint).hostname or "").strip().lower()
        if host in {"localhost", "host.docker.internal"} or host.endswith(".local"):
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return bool(host) and "." not in host
        return address.is_private or address.is_loopback or address.is_link_local


def build_hub_budgeted_worker_text_generation(
    *,
    client: HttpWorkflowHubDecisionClient,
    provider_urls: Mapping[str, object],
    transport: WorkerProviderTransportPort | None = None,
) -> HubBudgetedWorkerTextGeneration:
    """Production composition; the budget adapter always calls back to Hub."""

    if client is None:
        raise ValueError("worker_provider_hub_client_required")
    return HubBudgetedWorkerTextGeneration(
        provider_urls=provider_urls,
        budgets=HubProviderBudgetAdapter(client),
        transport=transport,
    )


__all__ = [
    "HubBudgetedWorkerTextGeneration",
    "WorkerProviderBudgetPort",
    "WorkerProviderHttpTransport",
    "WorkerProviderTransportPort",
    "build_hub_budgeted_worker_text_generation",
]
