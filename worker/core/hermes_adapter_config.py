from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


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

    @model_validator(mode="after")
    def _validate_enabled_requirements(self) -> "HermesAdapterConfig":
        if self.enabled and not self.default_model.strip():
            raise ValueError("default_model is required when Hermes is enabled")
        overlap = set(self.allowed_task_kinds) & set(self.blocked_task_kinds)
        if overlap:
            raise ValueError(f"allowed_task_kinds and blocked_task_kinds overlap: {sorted(overlap)}")
        return self

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
        }
