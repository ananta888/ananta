"""Hub-owned inference-target contract for coding-agent clients."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from agent.cli_backends.helpers import (
    _get_agent_config,
    _get_runtime_default_provider,
    _get_runtime_provider_urls,
    _is_probably_local_base_url,
    _normalize_ollama_openai_base_url,
    _normalize_openai_base_url,
    _resolve_profile_api_key,
)
from agent.config import settings
from agent.local_llm_backends import resolve_local_openai_backend

_HOSTED_PROVIDER_MODEL_PREFIXES = frozenset(
    {"anthropic", "gemini", "groq", "openai", "openrouter", "xai"}
)
_PROVIDER_API_KEY_ENVIRONMENT = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "xai": "XAI_API_KEY",
}


@dataclass(frozen=True, slots=True)
class CodingAgentInferenceTarget:
    """Resolved model endpoint, kept separate from the executing CLI client."""

    client_id: str
    provider_id: str | None
    model: str | None
    cli_model: str | None
    base_url: str | None
    target_kind: str | None
    provider_type: str | None = None
    api_key: str | None = None
    api_key_source: str | None = None

    def public_metadata(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "target_provider": self.provider_id,
            "target_model": self.model,
            "cli_model": self.cli_model,
            "target_base_url": self.base_url,
            "target_kind": self.target_kind,
            "target_provider_type": self.provider_type,
            "api_key_configured": bool(self.api_key),
            "api_key_source": self.api_key_source,
        }

    def process_environment(self) -> dict[str, str]:
        environment: dict[str, str] = {}
        if self.base_url:
            environment["OPENAI_API_BASE"] = self.base_url
            environment["OPENAI_BASE_URL"] = self.base_url
        if self.api_key:
            environment["OPENAI_API_KEY"] = self.api_key
        return environment


def resolve_aider_inference_target(
    model: str | None = None,
    *,
    agent_config: Mapping[str, object] | None = None,
    provider_urls: Mapping[str, object] | None = None,
    environment: Mapping[str, str] | None = None,
) -> CodingAgentInferenceTarget:
    """Resolve Aider's model provider from declarative Hub configuration."""

    cfg = dict(agent_config) if agent_config is not None else dict(_get_agent_config())
    urls = dict(provider_urls) if provider_urls is not None else dict(_get_runtime_provider_urls())
    source_environment = environment if environment is not None else os.environ
    aider_cfg = cfg.get("aider_cli") if isinstance(cfg.get("aider_cli"), dict) else {}
    default_provider = str(cfg.get("default_provider") or _get_runtime_default_provider() or "").strip().lower()
    configured_provider = str(aider_cfg.get("target_provider") or "").strip().lower() or None
    requested_model = str(
        model
        or aider_cfg.get("model")
        or aider_cfg.get("default_model")
        or getattr(settings, "aider_default_model", None)
        or cfg.get("default_model")
        or cfg.get("model")
        or ""
    ).strip() or None

    explicit_prefix, unprefixed_model = _split_recognized_model(
        requested_model,
        known_providers={*urls, *_HOSTED_PROVIDER_MODEL_PREFIXES, "ollama", "lmstudio"},
    )
    provider_id = configured_provider or explicit_prefix or default_provider or None
    target_model = unprefixed_model if explicit_prefix and explicit_prefix == provider_id else requested_model
    base_url: str | None = None
    provider_type: str | None = None
    api_key: str | None = None
    api_key_source: str | None = None

    local_target = resolve_local_openai_backend(
        provider_id,
        agent_cfg=cfg,
        provider_urls=urls,
        default_provider=default_provider,
        default_model=str(cfg.get("default_model") or ""),
    ) if provider_id else None
    if provider_id == "ollama":
        base_url = _normalize_ollama_openai_base_url(
            str(urls.get("ollama") or getattr(settings, "ollama_url", None) or "")
        )
        provider_type = "local_openai_compatible"
    elif provider_id == "lmstudio":
        base_url = _normalize_openai_base_url(
            str(urls.get("lmstudio") or getattr(settings, "lmstudio_url", None) or "")
        )
        provider_type = "local_openai_compatible"
    elif local_target:
        base_url = _normalize_openai_base_url(local_target.get("base_url"))
        provider_type = str(local_target.get("provider_type") or "local_openai_compatible")
        api_key = str(local_target.get("api_key") or "").strip() or None
        if api_key:
            api_key_source = f"local_openai.{provider_id}"
        elif local_target.get("api_key_profile"):
            api_key = _resolve_profile_api_key(local_target.get("api_key_profile"))
            if api_key:
                api_key_source = f"local_openai.{provider_id}.api_key_profile"
    elif provider_id:
        base_url = _normalize_openai_base_url(str(urls.get(provider_id) or ""))

    configured_profile = str(aider_cfg.get("api_key_profile") or "").strip()
    if not api_key and configured_profile:
        api_key = _resolve_profile_api_key(configured_profile)
        if api_key:
            api_key_source = "aider_cli.api_key_profile"
    provider_api_key_environment = _PROVIDER_API_KEY_ENVIRONMENT.get(str(provider_id or ""))
    if not api_key and provider_api_key_environment:
        settings_fallback = getattr(settings, "openai_api_key", None) if provider_id == "openai" else None
        api_key = str(source_environment.get(provider_api_key_environment) or settings_fallback or "").strip() or None
        if api_key:
            api_key_source = provider_api_key_environment.lower()
    if not api_key and base_url and _is_probably_local_base_url(base_url):
        api_key = "sk-no-key-needed"
        api_key_source = "local_dummy"

    target_kind = None
    if base_url:
        target_kind = "local_openai" if _is_probably_local_base_url(base_url) else "remote_openai_compatible"
    cli_model = _aider_cli_model(provider_id, target_model, bool(base_url))
    return CodingAgentInferenceTarget(
        client_id="aider",
        provider_id=provider_id,
        model=target_model,
        cli_model=cli_model,
        base_url=base_url,
        target_kind=target_kind,
        provider_type=provider_type,
        api_key=api_key,
        api_key_source=api_key_source,
    )


def _split_recognized_model(
    model: str | None,
    *,
    known_providers: set[str],
) -> tuple[str | None, str | None]:
    raw = str(model or "").strip()
    if "/" not in raw:
        return None, raw or None
    prefix, remainder = raw.split("/", 1)
    normalized_prefix = prefix.strip().lower()
    if normalized_prefix not in known_providers:
        return None, raw
    return normalized_prefix, remainder.strip() or None


def _aider_cli_model(provider_id: str | None, model: str | None, openai_compatible: bool) -> str | None:
    if not model:
        return None
    if openai_compatible:
        return model if model.startswith("openai/") else f"openai/{model}"
    if provider_id in _HOSTED_PROVIDER_MODEL_PREFIXES:
        return model if model.startswith(f"{provider_id}/") else f"{provider_id}/{model}"
    return model


__all__ = ["CodingAgentInferenceTarget", "resolve_aider_inference_target"]
