"""Bounded, secret-safe provider discovery for chat profile drafts."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProviderProbeResult:
    ok: bool
    error_code: str = ""
    models: tuple[str, ...] = ()
    model_found: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "error_code": self.error_code or None, "models": list(self.models), "model_found": self.model_found}


class ChatProviderProbe:
    SUPPORTED = {"lmstudio", "opencode", "hermes", "ollama", "ananta-worker"}

    def probe(self, draft: dict[str, Any], *, timeout_seconds: float = 2.5) -> ProviderProbeResult:
        backend = str(draft.get("chat_backend") or "ananta-worker")
        if backend not in self.SUPPORTED:
            return ProviderProbeResult(False, "unsupported_provider")
        credential_ref = str(draft.get("chat_backend_credential_ref") or "")
        if credential_ref:
            # No general credential resolver exists yet; never accept a secret in this adapter.
            return ProviderProbeResult(False, "credential_resolver_unavailable")
        base = str(draft.get("chat_backend_api_base") or "").rstrip("/")
        if not base:
            return ProviderProbeResult(False, "endpoint_required")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ProviderProbeResult(False, "invalid_endpoint")
        path = "/api/tags" if backend == "ollama" else "/v1/models"
        try:
            with urllib.request.urlopen(base + path, timeout=max(0.2, min(timeout_seconds, 5.0))) as response:
                payload = json.loads(response.read(1_000_000))
        except urllib.error.HTTPError as exc:
            return ProviderProbeResult(False, "auth_failed" if exc.code in {401, 403} else "provider_http_error")
        except (TimeoutError, socket.timeout):
            return ProviderProbeResult(False, "provider_timeout")
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return ProviderProbeResult(False, "endpoint_unreachable")
        raw_models = payload.get("models") if backend == "ollama" else payload.get("data")
        models = tuple(sorted({str(item.get("id") or item.get("name") or "") for item in raw_models or [] if isinstance(item, dict) and (item.get("id") or item.get("name"))}))
        requested = str(draft.get("chat_backend_model") or "")
        return ProviderProbeResult(True, models=models, model_found=(requested in models if requested else None))
