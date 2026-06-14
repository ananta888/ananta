"""
ModelProfileLoader — AMR-007

Loads and validates ModelProfile configs from JSON or YAML files.
Supports legacy planning_model_profiles.default.json format migration.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_PROVIDERS = frozenset({
    "ollama", "lmstudio", "openai", "openrouter", "openai_compatible", "mock", "fake",
})

ALLOWED_MODEL_ROLES = frozenset({
    "planner", "coder", "reviewer", "embedder", "summarizer", "chat", "any",
})


@dataclass
class ModelProfile:
    profile_id: str
    provider_id: str
    model: str
    model_role: str = "any"
    local: bool = False
    cloud: bool = False
    cloud_allowed: bool = False
    block_secret_context: bool = True
    supports_tools: bool = False
    supports_json: bool = False
    supports_streaming: bool = True
    context_tokens: int = 32768
    max_output_tokens: int = 2048
    timeout_seconds: int = 120
    temperature: float = 0.2
    cost_class: str = "free"
    quality_class: str = "medium"
    api_key_env: str | None = None
    base_url: str | None = None
    enabled: bool = True
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def is_cloud(self) -> bool:
        return self.cloud or self.provider_id in {"openai", "openrouter"}

    def is_usable_with_secrets(self) -> bool:
        """True if profile may receive secret-containing context."""
        return not self.block_secret_context or not self.is_cloud()


@dataclass
class ModelProfileLoadResult:
    profiles: list[ModelProfile]
    errors: list[str]
    source: str

    @property
    def ok(self) -> bool:
        return not self.errors


class ModelProfileLoader:
    """Loads ModelProfile list from a file path or raw dict."""

    def load_file(self, path: str | Path) -> ModelProfileLoadResult:
        p = Path(path)
        if not p.exists():
            return ModelProfileLoadResult([], [f"file_not_found:{p}"], str(p))
        try:
            raw = self._read(p)
        except Exception as exc:
            return ModelProfileLoadResult([], [f"parse_error:{exc}"], str(p))
        return self.load_dict(raw, source=str(p))

    def load_dict(self, data: dict[str, Any], *, source: str = "<dict>") -> ModelProfileLoadResult:
        errors: list[str] = []
        profiles: list[ModelProfile] = []
        raw_list = data.get("profiles") or []
        if not isinstance(raw_list, list):
            return ModelProfileLoadResult([], ["profiles_must_be_list"], source)
        seen_ids: set[str] = set()
        for i, raw in enumerate(raw_list):
            if not isinstance(raw, dict):
                errors.append(f"profile[{i}]:not_a_dict")
                continue
            result = self._parse_profile(raw, index=i)
            if result is None:
                errors.append(f"profile[{i}]:parse_failed")
                continue
            prof, prof_errors = result
            errors.extend(prof_errors)
            if prof_errors:
                continue
            if prof.profile_id in seen_ids:
                errors.append(f"profile[{i}]:duplicate_id:{prof.profile_id!r}")
                continue
            seen_ids.add(prof.profile_id)
            profiles.append(prof)
        return ModelProfileLoadResult(profiles=profiles, errors=errors, source=source)

    def migrate_legacy(self, legacy_data: dict[str, Any]) -> ModelProfileLoadResult:
        """Convert planning_model_profiles.default.json format to ModelProfile list."""
        raw_list = legacy_data.get("profiles") or []
        profiles: list[ModelProfile] = []
        errors: list[str] = []
        for i, raw in enumerate(raw_list):
            if not isinstance(raw, dict):
                continue
            provider = str(raw.get("provider") or "lmstudio").strip()
            model_pattern = str(raw.get("model_name_pattern") or "auto").strip()
            profile_name = str(raw.get("profile_name") or f"legacy_{i}").strip()
            profile = ModelProfile(
                profile_id=profile_name,
                provider_id=provider if provider != "*" else "lmstudio",
                model=model_pattern,
                model_role="any",
                local=provider not in {"openai", "openrouter"},
                cloud=provider in {"openai", "openrouter"},
                cloud_allowed=provider in {"openai", "openrouter"},
                block_secret_context=provider in {"openai", "openrouter"},
                supports_json="json" in str(raw.get("notes") or "").lower(),
                context_tokens=int(raw.get("context_max_chars") or 4096),
                max_output_tokens=int(raw.get("max_output_tokens") or 2048),
                temperature=float(raw.get("temperature") or 0.2),
                enabled=bool(raw.get("enabled", True)),
                notes=str(raw.get("notes") or ""),
                extra={"_legacy": True, "_source": "planning_model_profiles"},
            )
            profiles.append(profile)
        return ModelProfileLoadResult(profiles=profiles, errors=errors, source="<legacy>")

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        if path.suffix in {".yaml", ".yml"}:
            try:
                import yaml
                return yaml.safe_load(text) or {}
            except ImportError:
                raise ImportError("pyyaml required for YAML model profile files")
        return json.loads(text)

    @staticmethod
    def _parse_profile(raw: dict[str, Any], index: int) -> tuple[ModelProfile, list[str]] | None:
        errors: list[str] = []
        pid = str(raw.get("profile_id") or "").strip()
        if not pid:
            errors.append(f"profile[{index}]:missing_profile_id")
        provider = str(raw.get("provider_id") or "").strip()
        if not provider:
            errors.append(f"profile[{index}]:missing_provider_id")
        model = str(raw.get("model") or "").strip()
        if not model:
            errors.append(f"profile[{index}]:missing_model")
        if errors:
            return ModelProfile(
                profile_id=pid or f"__invalid_{index}",
                provider_id=provider or "unknown",
                model=model or "unknown",
            ), errors

        is_cloud = bool(raw.get("cloud", False)) or provider in {"openai", "openrouter"}

        # Security validation: cloud profiles must define cloud_allowed and block_secret_context
        if is_cloud and "cloud_allowed" not in raw:
            errors.append(f"profile[{index}]:{pid!r}:cloud_profile_missing_cloud_allowed")
        if is_cloud and "block_secret_context" not in raw:
            errors.append(f"profile[{index}]:{pid!r}:cloud_profile_missing_block_secret_context")

        known_keys = {
            "profile_id", "provider_id", "model", "model_role", "local", "cloud",
            "cloud_allowed", "block_secret_context", "supports_tools", "supports_json",
            "supports_streaming", "context_tokens", "max_output_tokens", "timeout_seconds",
            "temperature", "cost_class", "quality_class", "api_key_env", "base_url",
            "enabled", "notes",
        }
        extra = {k: v for k, v in raw.items() if k not in known_keys}

        try:
            profile = ModelProfile(
                profile_id=pid,
                provider_id=provider,
                model=model,
                model_role=str(raw.get("model_role") or "any"),
                local=bool(raw.get("local", not is_cloud)),
                cloud=is_cloud,
                cloud_allowed=bool(raw.get("cloud_allowed", False)),
                block_secret_context=bool(raw.get("block_secret_context", True)),
                supports_tools=bool(raw.get("supports_tools", False)),
                supports_json=bool(raw.get("supports_json", False)),
                supports_streaming=bool(raw.get("supports_streaming", True)),
                context_tokens=max(1, int(raw.get("context_tokens") or 4096)),
                max_output_tokens=max(1, int(raw.get("max_output_tokens") or 2048)),
                timeout_seconds=max(1, int(raw.get("timeout_seconds") or 120)),
                temperature=float(raw.get("temperature") or 0.2),
                cost_class=str(raw.get("cost_class") or "free"),
                quality_class=str(raw.get("quality_class") or "medium"),
                api_key_env=str(raw["api_key_env"]) if raw.get("api_key_env") else None,
                base_url=str(raw["base_url"]) if raw.get("base_url") else None,
                enabled=bool(raw.get("enabled", True)),
                notes=str(raw.get("notes") or ""),
                extra=extra,
            )
        except Exception as exc:
            errors.append(f"profile[{index}]:{pid!r}:field_error:{exc}")
            return None
        return profile, errors
