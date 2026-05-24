from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

_TRUST_LEVELS = {"local", "cloud"}
_UPSTREAM_TYPES = {
    "openai_compatible",
    "local_lmstudio",
    "ollama_openai_compat",
    "vllm",
    "openrouter_compatible",
}


class ListenConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    prefix: str = "/v1"

    @field_validator("port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if value < 1 or value > 65535:
            raise ValueError("listen.port must be in range 1..65535")
        return value

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text.startswith("/"):
            raise ValueError("listen.prefix must start with '/'")
        return text.rstrip("/") or "/v1"


class UpstreamConfig(BaseModel):
    id: str
    type: str = "openai_compatible"
    base_url: str
    api_key_env: str | None = None
    trust_level: Literal["local", "cloud"] = "local"
    allowed_models: list[str] = Field(default_factory=list)
    timeout_seconds: int = 60

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("upstream.id must be non-empty")
        return text

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if text not in _UPSTREAM_TYPES:
            raise ValueError(f"upstream.type must be one of {sorted(_UPSTREAM_TYPES)}")
        return text

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        text = str(value or "").strip()
        if not (text.startswith("http://") or text.startswith("https://")):
            raise ValueError("upstream.base_url must start with http:// or https://")
        return text.rstrip("/")

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, value: int) -> int:
        if value < 1 or value > 600:
            raise ValueError("upstream.timeout_seconds must be in range 1..600")
        return value


class RoutingRuleConfig(BaseModel):
    when: dict[str, Any] = Field(default_factory=dict)
    upstream: str
    model: str | None = None


class RoutingConfig(BaseModel):
    default_upstream: str
    default_model: str = "auto"
    model_aliases: dict[str, str] = Field(default_factory=dict)
    rules: list[RoutingRuleConfig] = Field(default_factory=list)


class PolicyConfig(BaseModel):
    default_action: str = "deny_if_unclassified"
    default_deny_secrets: bool = True
    cloud_context_default: str = "redacted_minimal"
    local_context_default: str = "allowed_by_context_gate"
    allow_worker_override_upstream: bool = False
    allow_prompt_override_policy: bool = False
    max_context_chars_default: int = 120000
    active_profile: str = "cloud_safe"
    profiles: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: {
            "local_dev": {
                "cloud_context_default": "redacted_minimal",
                "local_context_default": "allowed_by_context_gate",
                "deny_high_risk_to_cloud": True,
            },
            "cloud_safe": {
                "cloud_context_default": "redacted_minimal",
                "local_context_default": "allowed_by_context_gate",
                "deny_high_risk_to_cloud": True,
            },
            "high_risk": {
                "cloud_context_default": "none",
                "local_context_default": "minimal_local",
                "deny_high_risk_to_cloud": True,
            },
        }
    )

    @field_validator("max_context_chars_default")
    @classmethod
    def _validate_context_chars(cls, value: int) -> int:
        if value < 1000 or value > 2_000_000:
            raise ValueError("policy.max_context_chars_default must be in range 1000..2000000")
        return value


class RedactionConfig(BaseModel):
    enabled: bool = True
    block_private_keys: bool = True
    block_env_files: bool = True
    patterns: list[str] = Field(default_factory=list)


class ResponseValidationConfig(BaseModel):
    validate_openai_shape: bool = True
    validate_stream_chunks: bool = True
    structured_json_repair_attempts: int = 1

    @field_validator("structured_json_repair_attempts")
    @classmethod
    def _validate_repair_attempts(cls, value: int) -> int:
        if value < 0 or value > 2:
            raise ValueError("response_validation.structured_json_repair_attempts must be in range 0..2")
        return value


class LlmInterceptorConfig(BaseModel):
    listen: ListenConfig = Field(default_factory=ListenConfig)
    upstreams: list[UpstreamConfig]
    routing: RoutingConfig
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)
    response_validation: ResponseValidationConfig = Field(default_factory=ResponseValidationConfig)

    @field_validator("upstreams")
    @classmethod
    def _validate_upstreams(cls, value: list[UpstreamConfig]) -> list[UpstreamConfig]:
        if not value:
            raise ValueError("at least one upstream is required")
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("upstream ids must be unique")
        for item in value:
            if item.trust_level not in _TRUST_LEVELS:
                raise ValueError(f"invalid trust_level {item.trust_level!r}")
        return value

    @field_validator("routing")
    @classmethod
    def _validate_routing(cls, value: RoutingConfig, info) -> RoutingConfig:
        upstreams = info.data.get("upstreams") or []
        known_ids = {item.id for item in upstreams}
        if value.default_upstream not in known_ids:
            raise ValueError("routing.default_upstream must reference an existing upstream id")
        for rule in value.rules:
            if rule.upstream not in known_ids:
                raise ValueError(f"routing rule upstream {rule.upstream!r} not found in upstream list")
        return value


def load_llm_interceptor_config(path: str | Path) -> LlmInterceptorConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read interceptor config: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid interceptor config json: {exc}") from exc
    try:
        return LlmInterceptorConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid interceptor config: {exc}") from exc
