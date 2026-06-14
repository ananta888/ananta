"""
ModelMasterDefaultService — AMR-022

Provides the global master default ModelProfile synthesised from
ANANTA_MASTER_LLM_* or DEFAULT_* env vars.

Resolution order within this service:
  1. ANANTA_MASTER_LLM_PROVIDER / ANANTA_MASTER_LLM_MODEL (new, preferred)
  2. DEFAULT_PROVIDER / DEFAULT_MODEL (legacy fallback)

The returned profile has profile_id="_global_master_default" and is used
only at rank "global_master_default" in ModelProfileResolver.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from agent.services.model_profile_loader import ModelProfile

logger = logging.getLogger(__name__)

_GLOBAL_MASTER_PROFILE_ID = "_global_master_default"


class ModelMasterDefaultService:
    """Reads global master LLM defaults from env vars and produces a ModelProfile."""

    def __init__(self, env: dict[str, str] | None = None):
        self._env = dict(os.environ if env is None else env)

    def get_master_profile(self) -> ModelProfile | None:
        provider, model, base_url, api_key = self._read_master_env()

        if not provider and not model:
            return None

        effective_provider = provider or "lmstudio"
        effective_model = model or "auto"

        if effective_provider in ("openai", "openrouter"):
            cloud = True
            cloud_allowed = True
            block_secret_context = True
            local = False
        else:
            cloud = False
            cloud_allowed = False
            block_secret_context = False
            local = True

        profile = ModelProfile(
            profile_id=_GLOBAL_MASTER_PROFILE_ID,
            provider_id=effective_provider,
            model=effective_model,
            model_role="any",
            local=local,
            cloud=cloud,
            cloud_allowed=cloud_allowed,
            block_secret_context=block_secret_context,
            supports_tools=True,
            supports_json=True,
            supports_streaming=True,
            context_tokens=32768,
            max_output_tokens=4096,
            timeout_seconds=120,
            temperature=0.2,
            base_url=base_url,
        )

        profile.extra["_master_default_source"] = self._source_label()
        if api_key:
            profile.extra["_master_default_api_key"] = api_key

        logger.debug(
            "model_master_default: synthesised profile provider=%s model=%s source=%s",
            effective_provider, effective_model, self._source_label(),
        )
        return profile

    def get_master_provider(self) -> str | None:
        provider, _, _, _ = self._read_master_env()
        return provider

    def get_master_model(self) -> str | None:
        _, model, _, _ = self._read_master_env()
        return model

    def get_master_base_url(self) -> str | None:
        _, _, base_url, _ = self._read_master_env()
        return base_url

    def get_master_api_key(self) -> str | None:
        _, _, _, api_key = self._read_master_env()
        return api_key

    def _read_master_env(self) -> tuple[str | None, str | None, str | None, str | None]:
        ananta_provider = str(self._env.get("ANANTA_MASTER_LLM_PROVIDER") or "").strip() or None
        ananta_model = str(self._env.get("ANANTA_MASTER_LLM_MODEL") or "").strip() or None
        ananta_base_url = str(self._env.get("ANANTA_MASTER_LLM_BASE_URL") or "").strip() or None
        ananta_api_key = str(self._env.get("ANANTA_MASTER_LLM_API_KEY") or "").strip() or None

        legacy_provider = str(self._env.get("DEFAULT_PROVIDER") or "").strip() or None
        legacy_model = str(self._env.get("DEFAULT_MODEL") or "").strip() or None

        provider = ananta_provider or legacy_provider
        model = ananta_model or legacy_model
        base_url = ananta_base_url or None
        api_key = ananta_api_key or None

        return provider, model, base_url, api_key

    def _source_label(self) -> str:
        env = self._env
        if env.get("ANANTA_MASTER_LLM_PROVIDER") or env.get("ANANTA_MASTER_LLM_MODEL"):
            return "ANANTA_MASTER_LLM_*"
        if env.get("DEFAULT_PROVIDER") or env.get("DEFAULT_MODEL"):
            return "DEFAULT_PROVIDER/DEFAULT_MODEL"
        return "none"

    def read_model(self) -> dict[str, Any]:
        provider, model, base_url, api_key = self._read_master_env()
        source = self._source_label()
        has_ananta = bool(self._env.get("ANANTA_MASTER_LLM_PROVIDER") or self._env.get("ANANTA_MASTER_LLM_MODEL"))
        has_legacy = bool(self._env.get("DEFAULT_PROVIDER") or self._env.get("DEFAULT_MODEL"))
        warnings: list[str] = []
        if has_ananta and has_legacy:
            warnings.append(
                "Both ANANTA_MASTER_LLM_* and DEFAULT_PROVIDER/DEFAULT_MODEL are set. "
                "ANANTA_MASTER_LLM_* takes precedence."
            )
        return {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key_configured": bool(api_key),
            "source": source,
            "has_ananta_master": has_ananta,
            "has_legacy_default": has_legacy,
            "warnings": warnings,
        }


_GLOBAL_MASTER_SERVICE = ModelMasterDefaultService()


def get_global_master_default_service() -> ModelMasterDefaultService:
    return _GLOBAL_MASTER_SERVICE
