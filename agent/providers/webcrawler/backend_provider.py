"""OpenAI-compatible backend adapter for the external ananta-webcrawler."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.providers.interfaces import ProviderDescriptor, ProviderHealthReport
from agent.providers.redaction import redact_provider_payload

from .config import AnantaWebcrawlerProviderConfig
from .transport import (
    UrllibWebcrawlerHttpTransport,
    WebcrawlerHttpTransportPort,
    WebcrawlerTransportError,
)

MAX_REQUEST_CONTENT_BYTES = 4 * 1024 * 1024

_HTTP_REASON_CODES = {
    401: "webcrawler_authentication_failed",
    403: "webcrawler_policy_forbidden",
    404: "webcrawler_profile_not_found",
    409: "webcrawler_profile_draft",
    422: "webcrawler_profile_invalid",
}


class WebcrawlerProviderError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int | None = None) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class WebcrawlerCompletion:
    content: str
    profile: str
    tool_results: tuple[dict[str, Any], ...]
    diagnostics: dict[str, Any]


class AnantaWebcrawlerBackendProvider:
    descriptor = ProviderDescriptor(
        provider_id="ananta_webcrawler_openai",
        provider_family="model_backend",
        capabilities=("chat", "streaming", "tools", "web", "browser", "replay"),
        risk_class="high",
        enabled_by_default=False,
        display_name="Ananta Webcrawler (external)",
        notes="The model identifier is an external Webcrawler profile, not a general LLM model.",
    )

    def __init__(
        self,
        config: AnantaWebcrawlerProviderConfig,
        transport: WebcrawlerHttpTransportPort | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibWebcrawlerHttpTransport()

    def health(self) -> ProviderHealthReport:
        if not self._config.enabled or self._config.mode == "disabled":
            return ProviderHealthReport(status="disabled", reason="webcrawler_disabled")
        try:
            profiles = self.list_profiles()
        except WebcrawlerProviderError as exc:
            return ProviderHealthReport(
                status="degraded",
                reason=exc.reason_code,
                details={"mode": self._config.mode},
            )
        return ProviderHealthReport(
            status="healthy",
            details={"mode": self._config.mode, "profile_count": len(profiles)},
        )

    def list_profiles(self) -> list[dict[str, Any]]:
        payload = self._request("GET", self._config.healthcheck_path)
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise WebcrawlerProviderError("webcrawler_models_contract_invalid")
        profiles: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            profile_id = str(row.get("id") or "").strip()
            if not self._config.profile_allowed(profile_id):
                continue
            profiles.append(
                {
                    "id": profile_id,
                    "object": str(row.get("object") or "model"),
                    "status": str(row.get("status") or "unknown"),
                    "success_rate": row.get("success_rate")
                    if isinstance(row.get("success_rate"), (int, float))
                    and not isinstance(row.get("success_rate"), bool)
                    else None,
                    "provider": self.descriptor.provider_id,
                    "model_semantics": "profile_name",
                }
            )
        return sorted(profiles, key=lambda item: item["id"])

    def complete(
        self,
        *,
        profile: str,
        messages: Sequence[Mapping[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> WebcrawlerCompletion:
        self._require_profile(profile)
        request: dict[str, Any] = {
            "model": profile,
            "messages": self._messages(messages),
            "stream": False,
        }
        if temperature is not None:
            request["temperature"] = temperature
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        response = self._request("POST", "/chat/completions", request)
        return self._completion(response, profile)

    def stream(
        self,
        *,
        profile: str,
        messages: Sequence[Mapping[str, Any]],
    ) -> Iterable[dict[str, Any]]:
        self._require_profile(profile)
        payload = {"model": profile, "messages": self._messages(messages), "stream": True}
        try:
            chunks = self._transport.stream_sse(
                self._config.endpoint("/chat/completions"),
                headers=self._headers(),
                payload=payload,
                timeout=self._config.request_timeout_seconds,
            )
            for chunk in chunks:
                yield dict(redact_provider_payload(dict(chunk)))
        except WebcrawlerTransportError as exc:
            raise self._provider_error(exc) from exc

    def adapter_action(self, *, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Invoke one policy-authorized adapter endpoint without browser logic."""

        if not path.startswith("/adapter/") or path.count("/") > 3:
            raise WebcrawlerProviderError("webcrawler_adapter_path_invalid")
        return dict(self._request("POST", path, payload))

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        try:
            return self._transport.request_json(
                method,
                self._config.endpoint(path),
                headers=self._headers(),
                payload=payload,
                timeout=self._config.request_timeout_seconds,
            )
        except WebcrawlerTransportError as exc:
            raise self._provider_error(exc) from exc

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._config.api_key_env:
            api_key = os.environ.get(self._config.api_key_env, "").strip()
            if not api_key:
                raise WebcrawlerProviderError("webcrawler_api_key_unavailable")
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _require_profile(self, profile: str) -> None:
        if not self._config.enabled:
            raise WebcrawlerProviderError("webcrawler_disabled")
        if not self._config.profile_allowed(profile):
            raise WebcrawlerProviderError("webcrawler_profile_policy_blocked")

    @staticmethod
    def _messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 256:
            raise WebcrawlerProviderError("webcrawler_messages_invalid")
        normalized: list[dict[str, Any]] = []
        total_content_bytes = 0
        for message in messages:
            if not isinstance(message, Mapping):
                raise WebcrawlerProviderError("webcrawler_messages_invalid")
            role = str(message.get("role") or "").strip()
            content = message.get("content")
            if role not in {"system", "user", "assistant", "tool"} or not isinstance(content, str):
                raise WebcrawlerProviderError("webcrawler_messages_invalid")
            if len(content) > 1_000_000:
                raise WebcrawlerProviderError("webcrawler_messages_too_large")
            total_content_bytes += len(content.encode("utf-8"))
            if total_content_bytes > MAX_REQUEST_CONTENT_BYTES:
                raise WebcrawlerProviderError("webcrawler_messages_too_large")
            normalized.append(dict(message))
        return normalized

    @staticmethod
    def _completion(response: Mapping[str, Any], profile: str) -> WebcrawlerCompletion:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise WebcrawlerProviderError("webcrawler_completion_contract_invalid")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise WebcrawlerProviderError("webcrawler_completion_contract_invalid")
        content = message.get("content")
        if not isinstance(content, str):
            raise WebcrawlerProviderError("webcrawler_completion_contract_invalid")
        raw_tools = response.get("tool_results", message.get("tool_results", []))
        if not isinstance(raw_tools, list):
            raise WebcrawlerProviderError("webcrawler_tool_results_contract_invalid")
        tool_results = tuple(
            dict(redact_provider_payload(dict(item))) for item in raw_tools if isinstance(item, Mapping)
        )
        diagnostics = redact_provider_payload(
            {
                "id": response.get("id"),
                "usage": response.get("usage"),
                "finish_reason": choices[0].get("finish_reason"),
            }
        )
        return WebcrawlerCompletion(content, profile, tool_results, dict(diagnostics))

    @staticmethod
    def _provider_error(error: WebcrawlerTransportError) -> WebcrawlerProviderError:
        status = error.status_code
        if status is not None:
            reason = _HTTP_REASON_CODES.get(
                status, "webcrawler_execution_failed" if status >= 500 else "webcrawler_http_error"
            )
            return WebcrawlerProviderError(reason, status_code=status)
        return WebcrawlerProviderError(error.reason_code)
