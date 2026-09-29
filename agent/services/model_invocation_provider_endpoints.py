"""Provider endpoint resolution of ModelInvocationService: map profiles,
settings and runtime handoff registrations to chat-completions URLs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ananta_contracts.provider_endpoint_policy import (
    build_provider_request_url,
)


class ModelInvocationProviderEndpointMixin:
    """Resolve (provider, url, api_key) for one invocation attempt."""

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

    @staticmethod
    def _configured_local_backend_url(provider: str) -> str | None:
        """Chat-completions URL of a configured local OpenAI-compatible backend (e.g. ``llamacpp``).

        Without this, a default provider such as ``llamacpp`` fell through to ``lmstudio_url``
        and was sent to LM Studio's endpoint instead of its own ``local_openai_backends`` entry.
        """
        try:
            from flask import current_app, has_app_context

            from agent.local_llm_backends import resolve_local_openai_backend

            if not has_app_context():
                return None
            entry = resolve_local_openai_backend(
                provider,
                agent_cfg=dict(current_app.config.get("AGENT_CONFIG", {}) or {}),
                provider_urls=dict(current_app.config.get("PROVIDER_URLS", {}) or {}),
            )
        except Exception:  # noqa: BLE001 -- resolution is best effort; the caller keeps its fallback
            return None
        base = str((entry or {}).get("base_url") or "").rstrip("/")
        if not base or provider in ("lmstudio", "lm_studio"):
            return None
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

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

        configured = cls._configured_local_backend_url(provider)
        if configured:
            return provider, configured, None

        # Generic OpenAI-compatible fallback
        base = s.lmstudio_url.rstrip("/")
        if not base.endswith("/chat/completions"):
            base = base + "/chat/completions"
        return provider, base, None
