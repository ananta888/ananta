"""
ModelOverrideNormalizationService — AMR-009

Normalizes legacy DEFAULT_PROVIDER / DEFAULT_MODEL env vars and old
planning_model_profiles.default.json entries into the new ModelProfile schema.

Also resolves the effective profile_id when a caller has only a provider+model string.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Any

from agent.services.model_profile_loader import ModelProfile, ModelProfileLoader

logger = logging.getLogger(__name__)

# Canonical mapping from legacy provider strings to new provider_id values
_LEGACY_PROVIDER_MAP: dict[str, str] = {
    "lmstudio": "lmstudio",
    "lm_studio": "lmstudio",
    "lm-studio": "lmstudio",
    "ollama": "ollama",
    "openai": "openai",
    "openrouter": "openrouter",
    "open_router": "openrouter",
    "openai-compatible": "openai_compatible",
    "openai_compatible": "openai_compatible",
    "local": "lmstudio",
    "local_default": "lmstudio",
    "*": "lmstudio",
}


@dataclass
class NormalizationResult:
    profile: ModelProfile
    original_provider: str
    original_model: str
    warnings: list[str]


class ModelOverrideNormalizationService:
    """
    Converts legacy override formats to ModelProfile objects.

    Usage:
        svc = ModelOverrideNormalizationService()
        result = svc.from_env()
        result = svc.from_legacy_dict({"provider": "lmstudio", "model": "my-model"})
    """

    def __init__(self, loader: ModelProfileLoader | None = None):
        self._loader = loader or ModelProfileLoader()

    def from_env(self) -> NormalizationResult | None:
        """
        Read DEFAULT_PROVIDER and DEFAULT_MODEL from env.
        Returns None if neither is set.
        """
        provider = os.environ.get("DEFAULT_PROVIDER", "").strip()
        model = os.environ.get("DEFAULT_MODEL", "").strip()
        if not provider and not model:
            return None
        return self._build(
            provider=provider or "lmstudio",
            model=model or "auto",
            source="env:DEFAULT_PROVIDER/DEFAULT_MODEL",
        )

    def from_legacy_dict(self, data: dict[str, Any]) -> NormalizationResult | None:
        """
        Convert a single entry from planning_model_profiles.default.json profiles[].
        Returns None if data is empty or unusable.
        """
        provider = str(data.get("provider") or data.get("provider_id") or "").strip()
        model = str(
            data.get("model")
            or data.get("model_name_pattern")
            or data.get("model_id")
            or ""
        ).strip()
        if not provider and not model:
            return None
        return self._build(
            provider=provider or "lmstudio",
            model=model or "auto",
            source="legacy_planning_model_profiles",
            extra_data=data,
        )

    def from_profile_id_string(self, profile_str: str) -> NormalizationResult | None:
        """
        Accept strings like "lmstudio::my-model" or "ollama/llama3" as a profile hint.
        Returns None if string is empty or unrecognisable.
        """
        if not profile_str or not profile_str.strip():
            return None
        s = profile_str.strip()
        provider, _, model = s.partition("::")
        if not model:
            provider, _, model = s.partition("/")
        if not model:
            provider = "lmstudio"
            model = s
        return self._build(provider=provider.strip(), model=model.strip(), source=f"string:{s!r}")

    def normalize_provider_id(self, raw: str) -> str:
        """Map legacy provider string to canonical provider_id."""
        return _LEGACY_PROVIDER_MAP.get(raw.strip().lower(), raw.strip().lower())

    def _build(
        self,
        *,
        provider: str,
        model: str,
        source: str,
        extra_data: dict[str, Any] | None = None,
    ) -> NormalizationResult:
        warnings: list[str] = []
        original_provider = provider
        normalized_provider = self.normalize_provider_id(provider)
        if normalized_provider != provider:
            warnings.append(
                f"legacy_provider_normalized:{provider!r}→{normalized_provider!r}"
            )

        extra = extra_data or {}
        is_cloud = normalized_provider in {"openai", "openrouter"}

        # Cloud profiles synthesized from legacy sources need explicit opt-in warning
        if is_cloud and not extra.get("cloud_allowed"):
            warnings.append(
                f"legacy_cloud_profile:{normalized_provider!r} — "
                "cloud_allowed not explicitly set; defaulting to True for migration. "
                "Review and set cloud_allowed explicitly in new profile config."
            )

        profile = ModelProfile(
            profile_id=f"_legacy_{normalized_provider}_{model}".replace("/", "_").replace(":", "-"),
            provider_id=normalized_provider,
            model=model,
            model_role="any",
            local=not is_cloud,
            cloud=is_cloud,
            cloud_allowed=is_cloud,
            block_secret_context=is_cloud,
            supports_json="json" in str(extra.get("notes") or "").lower(),
            context_tokens=max(512, int(extra.get("context_max_chars") or 4096)),
            max_output_tokens=max(1, int(extra.get("max_output_tokens") or 2048)),
            temperature=float(extra.get("temperature") or 0.2),
            enabled=bool(extra.get("enabled", True)),
            notes=str(extra.get("notes") or f"Synthesized from legacy config via {source}"),
            extra={"_legacy": True, "_source": source},
        )

        logger.debug(
            "model_override_normalization: %s → profile_id=%s (warnings=%d)",
            source,
            profile.profile_id,
            len(warnings),
        )
        return NormalizationResult(
            profile=profile,
            original_provider=original_provider,
            original_model=model,
            warnings=warnings,
        )
