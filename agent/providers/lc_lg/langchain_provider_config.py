"""LangChainProviderConfig — serialisierbar, validierbar (LCG-003)."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


LangChainMode = Literal["disabled", "dry_run", "mock_live", "local_live", "cloud_gated"]
RetrieverSource = Literal["codecompass", "none"]


class LangChainProviderConfig(BaseModel):
    """Configuration for the optional LangChain worker adapter.

    Default: disabled and dry_run — no live execution without explicit opt-in.
    Secrets are never stored in plain text; use secret_refs instead.
    """
    enabled: bool = False
    mode: LangChainMode = "dry_run"

    allowed_task_types: list[str] = Field(
        default_factory=lambda: ["rag_query", "summarize", "tool_chain", "code_review"]
    )
    retriever_source: RetrieverSource = "codecompass"
    embedding_provider_scope: str = "codecompass_vector"
    model_provider_ref: str = "local.default"

    # Security
    external_calls_allowed: bool = False
    allowed_base_urls: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    secret_refs: list[str] = Field(default_factory=list)   # references, never values

    # Limits
    timeout_seconds: int = 120
    max_steps: int = 12
    max_tokens: int | None = None

    artifact_first: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timeout_seconds")
    @classmethod
    def _positive_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("timeout_seconds must be positive")
        return v

    @field_validator("max_steps")
    @classmethod
    def _positive_steps(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_steps must be positive")
        return v

    @field_validator("model_provider_ref")
    @classmethod
    def _no_plain_secrets(cls, v: str) -> str:
        # Reject patterns that look like API keys inline
        if any(ch in v for ch in ("sk-", "Bearer ", "key=", "token=")):
            raise ValueError(
                "model_provider_ref must be a reference, not a secret value"
            )
        return v

    @field_validator("retriever_source")
    @classmethod
    def _valid_retriever(cls, v: str) -> str:
        allowed = {"codecompass", "none"}
        if v not in allowed:
            raise ValueError(f"retriever_source must be one of {allowed}")
        return v

    @model_validator(mode="after")
    def _cloud_requires_external_calls(self) -> "LangChainProviderConfig":
        if self.mode == "cloud_gated" and not self.external_calls_allowed:
            raise ValueError(
                "cloud_gated mode requires external_calls_allowed=true"
            )
        return self

    @classmethod
    def default_off(cls) -> "LangChainProviderConfig":
        return cls(enabled=False, mode="dry_run")

    def is_live(self) -> bool:
        return self.enabled and self.mode in ("local_live", "cloud_gated")

    def is_dry_run(self) -> bool:
        return self.mode in ("dry_run", "mock_live") or not self.enabled

    def to_safe_dict(self) -> dict[str, Any]:
        """Returns config without any secret-looking values."""
        d = self.model_dump()
        d.pop("secret_refs", None)   # refs are safe, but keep them out of casual logging
        return d
