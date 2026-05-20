from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class HermesModelSelectionPolicy(BaseModel):
    prefer_task_specific_model: bool = True
    require_free_model_suffix: bool = False
    allow_fallback_on_unavailable: bool = True
    reject_blocked_models: bool = True
    reject_mutation_tasks_for_hermes: bool = True
    allow_candidate_roles: bool = True
    allowed_task_kinds_for_hermes: list[str] = Field(default_factory=list)
    mutation_task_kinds: list[str] = Field(default_factory=lambda: ["patch_apply", "command_execute", "shell_execute", "shell_execution", "service_mutation", "config_mutation", "workspace_mutation", "file_mutation"])
    blocked_task_kinds: list[str] = Field(default_factory=list)


class HermesAdapterConfig(BaseModel):
    enabled: bool = False
    base_url: str = ""
    api_key_env: str = "HERMES_API_KEY"
    default_model: str = ""
    timeout_seconds: float = 20.0
    max_retries: int = 1
    allowed_task_kinds: list[str] = Field(default_factory=lambda: ["plan_only", "review", "summarize", "patch_propose", "research_limited"])
    blocked_task_kinds: list[str] = Field(default_factory=lambda: ["patch_apply", "command_execute"])
    cloud_allowed: bool = False
    max_context_chars: int = 12000
    strict_json_required: bool = True
    rollout_phase: str = "phase1"
    blocked_models: list[str] = Field(default_factory=list)
    parse_retry_enabled: bool = True
    # HF-T031: default False — must be explicitly enabled; prevents accidental Hermes enablement
    feature_flag_enabled: bool = False
    # HF-T014: conservative temperature for structured JSON tasks; None means use model default
    default_temperature: float | None = 0.1
    # HF-T014: max output tokens cap; None means no explicit cap (use model default)
    max_output_tokens: int | None = None
    task_kind_models: dict[str, str] = Field(default_factory=dict)
    fallback_free_models: dict[str, list[str]] | list[str] = Field(default_factory=dict)
    model_selection_policy: HermesModelSelectionPolicy = Field(default_factory=HermesModelSelectionPolicy)

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return value

    @field_validator("max_retries", "max_context_chars")
    @classmethod
    def _validate_positive_ints(cls, value: int) -> int:
        if value < 0:
            raise ValueError("value must be >= 0")
        return value

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return ""
        if not (normalized.startswith("http://") or normalized.startswith("https://")):
            raise ValueError("base_url must start with http:// or https://")
        return normalized.rstrip("/")

    @field_validator("task_kind_models")
    @classmethod
    def _validate_task_kind_models(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, model_id in (value or {}).items():
            task_kind = str(key or "").strip()
            candidate = str(model_id or "").strip()
            if not task_kind:
                raise ValueError("task_kind_models contains an empty task kind")
            if not candidate:
                raise ValueError(f"task_kind_models[{task_kind!r}] must be a non-empty model id")
            cleaned[task_kind] = candidate
        return cleaned

    @field_validator("fallback_free_models")
    @classmethod
    def _validate_fallback_free_models(cls, value: dict[str, list[str]] | list[str]) -> dict[str, list[str]] | list[str]:
        if isinstance(value, list):
            cleaned = [str(item or "").strip() for item in value if str(item or "").strip()]
            deduped = list(dict.fromkeys(cleaned))
            return deduped
        if isinstance(value, dict):
            normalized: dict[str, list[str]] = {}
            for key, raw_models in value.items():
                task_kind = str(key or "").strip()
                if not task_kind:
                    raise ValueError("fallback_free_models contains an empty task key")
                if not isinstance(raw_models, list):
                    raise ValueError(f"fallback_free_models[{task_kind!r}] must be a list")
                models = [str(item or "").strip() for item in raw_models if str(item or "").strip()]
                normalized[task_kind] = list(dict.fromkeys(models))
            return normalized
        raise ValueError("fallback_free_models must be list[str] or dict[str, list[str]]")

    @model_validator(mode="after")
    def _validate_enabled_requirements(self) -> "HermesAdapterConfig":
        if self.enabled and not self.default_model.strip():
            raise ValueError("default_model is required when Hermes is enabled")
        overlap = set(self.allowed_task_kinds) & set(self.blocked_task_kinds)
        if overlap:
            raise ValueError(f"allowed_task_kinds and blocked_task_kinds overlap: {sorted(overlap)}")
        return self

    def fallback_models_for_task_kind(self, task_kind: str) -> list[str]:
        key = str(task_kind or "").strip()
        if isinstance(self.fallback_free_models, list):
            return list(self.fallback_free_models)
        return list(self.fallback_free_models.get(key, []))

    def diagnostics_view(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "api_key_value": "[REDACTED]",
            "default_model": self.default_model,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "allowed_task_kinds": list(self.allowed_task_kinds),
            "blocked_task_kinds": list(self.blocked_task_kinds),
            "cloud_allowed": self.cloud_allowed,
            "max_context_chars": self.max_context_chars,
            "strict_json_required": self.strict_json_required,
            "rollout_phase": self.rollout_phase,
            "blocked_models": list(self.blocked_models),
            "parse_retry_enabled": self.parse_retry_enabled,
            "feature_flag_enabled": self.feature_flag_enabled,
            "default_temperature": self.default_temperature,
            "max_output_tokens": self.max_output_tokens,
            "task_kind_models": dict(self.task_kind_models),
            "fallback_free_models": self.fallback_free_models,
            "model_selection_policy": self.model_selection_policy.model_dump(),
            "read_only_policy_summary": {
                "allowed_task_kinds_for_hermes": list(self.model_selection_policy.allowed_task_kinds_for_hermes),
                "mutation_task_kinds": list(self.model_selection_policy.mutation_task_kinds),
                "blocked_task_kinds": list(dict.fromkeys(list(self.blocked_task_kinds) + list(self.model_selection_policy.blocked_task_kinds))),
            },
        }
