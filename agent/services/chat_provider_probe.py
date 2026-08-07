"""Bounded, secret-safe provider discovery for chat profile drafts."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from agent.config import settings
from agent.services.openai_credential_endpoint_binding import (
    OPENAI_API_KEY_REFERENCE,
    OpenAICredentialEndpointBindingError,
    bind_openai_credential_endpoint,
)


@dataclass(frozen=True)
class ProviderProbeResult:
    ok: bool
    error_code: str = ""
    models: tuple[str, ...] = ()
    model_found: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_code": self.error_code or None,
            "models": list(self.models),
            "model_found": self.model_found,
        }


class ChatProviderProbe:
    SUPPORTED = {"lmstudio", "opencode", "hermes", "ollama", "ananta-worker", "openai"}

    def probe(self, draft: dict[str, Any], *, timeout_seconds: float = 2.5) -> ProviderProbeResult:
        backend = str(draft.get("chat_backend") or "ananta-worker").strip().lower()
        if backend not in self.SUPPORTED:
            return ProviderProbeResult(False, "unsupported_provider")
        credential_ref = str(draft.get("chat_backend_credential_ref") or "").strip()
        base = str(draft.get("chat_backend_api_base") or "").rstrip("/")

        openai_binding = None
        if backend == "openai":
            try:
                openai_binding = bind_openai_credential_endpoint(
                    client_api_base=base,
                    trusted_api_url=str(settings.openai_url),
                    credential_ref=credential_ref,
                )
            except OpenAICredentialEndpointBindingError as exc:
                return ProviderProbeResult(False, exc.error_code)
        else:
            if not base:
                return ProviderProbeResult(False, "endpoint_required")
            if credential_ref == OPENAI_API_KEY_REFERENCE:
                return ProviderProbeResult(False, "unsupported_credential_reference")
            parsed = urlparse(base)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                return ProviderProbeResult(False, "invalid_endpoint")

        token = ""
        if credential_ref:
            if not credential_ref.startswith("env://"):
                return ProviderProbeResult(False, "unsupported_credential_reference")
            token = os.environ.get(credential_ref.removeprefix("env://"), "")
            if not token:
                return ProviderProbeResult(False, "credential_not_configured")
        elif backend == "openai":
            token = os.environ.get("OPENAI_API_KEY", "")
            if not token:
                return ProviderProbeResult(False, "credential_not_configured")
        probe_url = (
            openai_binding.models_url
            if openai_binding
            else base + ("/api/tags" if backend == "ollama" else ("/models" if base.endswith("/v1") else "/v1/models"))
        )
        try:
            probe_request = urllib.request.Request(
                probe_url, headers=({"Authorization": f"Bearer {token}"} if token else {})
            )
            with urllib.request.urlopen(probe_request, timeout=max(0.2, min(timeout_seconds, 5.0))) as response:
                payload = json.loads(response.read(1_000_000))
        except urllib.error.HTTPError as exc:
            return ProviderProbeResult(False, "auth_failed" if exc.code in {401, 403} else "provider_http_error")
        except (TimeoutError, socket.timeout):
            return ProviderProbeResult(False, "provider_timeout")
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return ProviderProbeResult(False, "endpoint_unreachable")
        raw_models = payload.get("models") if backend == "ollama" else payload.get("data")
        models = tuple(
            sorted(
                {
                    str(item.get("id") or item.get("name") or "")
                    for item in raw_models or []
                    if isinstance(item, dict) and (item.get("id") or item.get("name"))
                }
            )
        )
        requested = str(draft.get("chat_backend_model") or "")
        return ProviderProbeResult(True, models=models, model_found=(requested in models if requested else None))
