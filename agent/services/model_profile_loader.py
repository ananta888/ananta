"""
ModelProfileLoader — AMR-007

Loads and validates ModelProfile configs from JSON or YAML files.
Supports legacy planning_model_profiles.default.json format migration.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_PROVIDERS = frozenset({
    "ollama", "lmstudio", "openai", "openrouter", "openai_compatible", "mock", "fake",
})

ALLOWED_MODEL_ROLES = frozenset({
    "planner", "coder", "reviewer", "embedder", "summarizer", "chat", "reasoning", "any",
})

ALLOWED_TOOL_CALLING_MODES = frozenset({"native_tools", "prompt_json", "both", "none"})
ALLOWED_JSON_RELIABILITY_CLASSES = frozenset({"unknown", "experimental", "usable", "strict"})
ALLOWED_CAPABILITY_VALUES = frozenset({"supported", "unsupported", "unknown"})
ALLOWED_CAPABILITY_EVIDENCE = frozenset({"declared", "detected", "benchmark", "unknown"})
ALLOWED_RELEASE_STATES = frozenset({
    "unavailable", "experimental", "shadow", "opt_in", "preferred", "disabled",
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
    supports_seed: bool = True
    context_tokens: int = 32768
    max_output_tokens: int = 2048
    timeout_seconds: int = 120
    temperature: float = 0.2
    cost_class: str = "free"
    quality_class: str = "medium"
    price_input_per_million: float | None = None
    price_output_per_million: float | None = None
    estimated_latency_class: str = "unknown"
    json_reliability_class: str = "unknown"
    tool_calling_mode: str = "none"
    preferred_for: list[str] = field(default_factory=list)
    avoid_for: list[str] = field(default_factory=list)
    max_context_for_profile: int | None = None
    retry_budget: int = 0
    fallback_group: str | None = None
    fallback_rank: int | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    enabled: bool = True
    notes: str = ""
    system_prompt_prefix: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # T01/T02 — Token budget extension fields (all safe-defaulted)
    context_window_tokens: int | None = None
    hard_max_output_tokens: int | None = None
    tokenizer_strategy: str = "chars_per_token"   # "tiktoken_cl100k" | "tiktoken_llama3" | "chars_per_token"
    tokenizer_name: str | None = None
    input_cost_per_1m_tokens: float | None = None
    output_cost_per_1m_tokens: float | None = None
    aliases: list[str] = field(default_factory=list)
    input_modalities: list[str] = field(default_factory=list)
    output_modalities: list[str] = field(default_factory=list)
    capability_claims: list[dict[str, str]] = field(default_factory=list)
    nominal_context_tokens: int | None = None
    verified_context_tokens: int | None = None
    hardware_class: str | None = None
    artifact_sha256: str | None = None
    release_state: str = "experimental"

    def is_cloud(self) -> bool:
        return self.cloud or self.provider_id in {"openai", "openrouter"}

    def is_usable_with_secrets(self) -> bool:
        """True if profile may receive secret-containing context."""
        return not self.block_secret_context or not self.is_cloud()

    def supports_prompt_json_tools(self) -> bool:
        return self.tool_calling_mode in {"prompt_json", "both"}

    def supports_native_tools(self) -> bool:
        return self.supports_tools and self.tool_calling_mode in {"native_tools", "both"}

    def max_input_tokens(self) -> int:
        """Return the prompt budget after reserving completion capacity."""

        context_window = max(1, int(self.context_tokens or 1))
        if self.max_context_for_profile is not None:
            context_window = min(
                context_window,
                max(1, int(self.max_context_for_profile)),
            )
        return max(
            1,
            context_window
            - max(1, int(self.max_output_tokens or 1))
            - self.system_prompt_prefix_tokens(),
        )

    def system_prompt_prefix_tokens(self) -> int:
        """Conservatively reserve context for a configured prompt prefix."""

        prefix = str(self.system_prompt_prefix or "").strip()
        if not prefix:
            return 0
        return max(1, len(prefix.encode("utf-8")))


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
                tool_calling_mode="prompt_json" if "json" in str(raw.get("notes") or "").lower() else "none",
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
            "supports_streaming", "supports_seed", "context_tokens", "max_output_tokens", "timeout_seconds",
            "temperature", "cost_class", "quality_class", "price_input_per_million",
            "price_output_per_million", "estimated_latency_class", "json_reliability_class",
            "tool_calling_mode", "preferred_for", "avoid_for", "max_context_for_profile",
            "retry_budget", "fallback_group", "fallback_rank", "api_key_env",
            "base_url",
            "enabled", "notes", "system_prompt_prefix",
            # T02 — token budget extension fields
            "context_window_tokens", "hard_max_output_tokens", "tokenizer_strategy",
            "tokenizer_name", "input_cost_per_1m_tokens", "output_cost_per_1m_tokens",
            "aliases", "input_modalities", "output_modalities", "capability_claims",
            "nominal_context_tokens", "verified_context_tokens", "hardware_class",
            "artifact_sha256", "release_state",
        }
        extra = {k: v for k, v in raw.items() if k not in known_keys}
        tool_calling_mode = str(
            raw.get("tool_calling_mode")
            or ("native_tools" if raw.get("supports_tools") else "none")
        ).strip()
        if tool_calling_mode not in ALLOWED_TOOL_CALLING_MODES:
            errors.append(f"profile[{index}]:{pid!r}:invalid_tool_calling_mode:{tool_calling_mode}")
        json_reliability_class = str(raw.get("json_reliability_class") or "unknown").strip()
        if json_reliability_class not in ALLOWED_JSON_RELIABILITY_CLASSES:
            errors.append(f"profile[{index}]:{pid!r}:invalid_json_reliability_class:{json_reliability_class}")
        release_state = str(raw.get("release_state") or "experimental").strip()
        if release_state not in ALLOWED_RELEASE_STATES:
            errors.append(f"profile[{index}]:{pid!r}:invalid_release_state:{release_state}")

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
                supports_seed=bool(raw.get("supports_seed", True)),
                context_tokens=max(1, int(raw.get("context_tokens") or 4096)),
                max_output_tokens=max(1, int(raw.get("max_output_tokens") or 2048)),
                timeout_seconds=max(1, int(raw.get("timeout_seconds") or 120)),
                temperature=float(raw.get("temperature") or 0.2),
                cost_class=str(raw.get("cost_class") or "free"),
                quality_class=str(raw.get("quality_class") or "medium"),
                price_input_per_million=_optional_float(raw.get("price_input_per_million")),
                price_output_per_million=_optional_float(raw.get("price_output_per_million")),
                estimated_latency_class=str(raw.get("estimated_latency_class") or "unknown"),
                json_reliability_class=json_reliability_class,
                tool_calling_mode=tool_calling_mode,
                preferred_for=_string_list(raw.get("preferred_for")),
                avoid_for=_string_list(raw.get("avoid_for")),
                max_context_for_profile=_optional_int(raw.get("max_context_for_profile")),
                retry_budget=max(0, int(raw.get("retry_budget") or 0)),
                fallback_group=str(raw["fallback_group"]) if raw.get("fallback_group") else None,
                fallback_rank=_optional_int(raw.get("fallback_rank")),
                api_key_env=str(raw["api_key_env"]) if raw.get("api_key_env") else None,
                base_url=str(raw["base_url"]) if raw.get("base_url") else None,
                enabled=bool(raw.get("enabled", True)),
                notes=str(raw.get("notes") or ""),
                system_prompt_prefix=_optional_prompt_prefix(
                    raw.get("system_prompt_prefix")
                ),
                extra=extra,
                # T02 — token budget extension fields
                context_window_tokens=_optional_int(raw.get("context_window_tokens")),
                hard_max_output_tokens=_optional_int(raw.get("hard_max_output_tokens")),
                tokenizer_strategy=str(raw.get("tokenizer_strategy") or "chars_per_token"),
                tokenizer_name=str(raw["tokenizer_name"]) if raw.get("tokenizer_name") else None,
                input_cost_per_1m_tokens=_optional_float(raw.get("input_cost_per_1m_tokens")),
                output_cost_per_1m_tokens=_optional_float(raw.get("output_cost_per_1m_tokens")),
                aliases=_bounded_identifier_list(raw.get("aliases"), field_name="aliases"),
                input_modalities=_bounded_identifier_list(
                    raw.get("input_modalities"), field_name="input_modalities"
                ),
                output_modalities=_bounded_identifier_list(
                    raw.get("output_modalities"), field_name="output_modalities"
                ),
                capability_claims=_capability_claims(raw.get("capability_claims")),
                nominal_context_tokens=_optional_int(raw.get("nominal_context_tokens")),
                verified_context_tokens=_optional_int(raw.get("verified_context_tokens")),
                hardware_class=_optional_bounded_identifier(raw.get("hardware_class")),
                artifact_sha256=_optional_sha256(raw.get("artifact_sha256")),
                release_state=release_state,
            )
            if (
                profile.nominal_context_tokens is not None
                and profile.verified_context_tokens is not None
                and profile.verified_context_tokens > profile.nominal_context_tokens
            ):
                raise ValueError("verified_context_tokens exceeds nominal_context_tokens")
        except Exception as exc:
            errors.append(f"profile[{index}]:{pid!r}:field_error:{exc}")
            return ModelProfile(
                profile_id=pid,
                provider_id=provider,
                model=model,
            ), errors
        return profile, errors


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_prompt_prefix(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if "\x00" in normalized or len(normalized.encode("utf-8")) > 1_024:
        raise ValueError("system_prompt_prefix must be at most 1024 bytes without NUL")
    return normalized


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _bounded_identifier_list(value: Any, *, field_name: str) -> list[str]:
    values = _string_list(value)
    normalized = list(dict.fromkeys(values))
    if len(normalized) > 64 or any(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}", item) is None
        for item in normalized
    ):
        raise ValueError(f"{field_name} contains invalid identifiers")
    return normalized


def _capability_claims(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("capability_claims must be a bounded list")
    claims: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) - {"capability_id", "value", "evidence", "source_id"}:
            raise ValueError("capability_claim_invalid")
        capability_id = str(raw.get("capability_id") or "").strip().lower()
        claim_value = str(raw.get("value") or "unknown").strip().lower()
        evidence = str(raw.get("evidence") or "unknown").strip().lower()
        source_id = str(raw.get("source_id") or "").strip()
        if (
            re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,63}", capability_id) is None
            or capability_id in seen
            or claim_value not in ALLOWED_CAPABILITY_VALUES
            or evidence not in ALLOWED_CAPABILITY_EVIDENCE
            or source_id
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}", source_id) is None
        ):
            raise ValueError("capability_claim_invalid")
        seen.add(capability_id)
        claims.append({
            "capability_id": capability_id,
            "value": claim_value,
            "evidence": evidence,
            **({"source_id": source_id} if source_id else {}),
        })
    return claims


def _optional_bounded_identifier(value: Any) -> str | None:
    if value is None or value == "":
        return None
    normalized = str(value).strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}", normalized) is None:
        raise ValueError("bounded identifier invalid")
    return normalized


def _optional_sha256(value: Any) -> str | None:
    if value is None or value == "":
        return None
    normalized = str(value).strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ValueError("artifact_sha256 invalid")
    return normalized
