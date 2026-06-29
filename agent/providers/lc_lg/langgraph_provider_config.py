"""LangGraphProviderConfig — serialisierbar, validierbar (LCG-004)."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


LangGraphMode = Literal["disabled", "dry_run", "mock_live", "local_live", "cloud_gated"]
CheckpointPolicy = Literal["local_ephemeral", "local_ephemeral_or_hub_owned", "hub_owned", "none"]
StatePolicy = Literal["external_state_cache_only", "hub_owned", "ephemeral"]

# Actions that always need human approval before live execution
DEFAULT_HUMAN_REQUIRED = ("write", "delete", "network", "shell", "patch", "push")


class LangGraphProviderConfig(BaseModel):
    """Configuration for the optional LangGraph worker adapter.

    Default: disabled and dry_run. External state is cache_only unless
    explicitly overridden. Hub task state is never overwritten.
    """
    enabled: bool = False
    mode: LangGraphMode = "dry_run"

    allowed_task_types: list[str] = Field(
        default_factory=lambda: [
            "agent_workflow", "multi_step_plan", "human_in_loop", "stateful_task"
        ]
    )
    allowed_graphs: list[str] = Field(default_factory=list)   # empty = any

    # State management
    state_policy: StatePolicy = "external_state_cache_only"
    checkpoint_policy: CheckpointPolicy = "local_ephemeral_or_hub_owned"

    # Human-in-the-loop
    human_in_loop_required_for: list[str] = Field(
        default_factory=lambda: list(DEFAULT_HUMAN_REQUIRED)
    )

    # Limits — prevent infinite loops
    timeout_seconds: int = 300
    max_iterations: int = 30
    max_nodes: int = 25
    max_tokens: int | None = None

    # Security
    external_calls_allowed: bool = False
    model_provider_ref: str = "local.default"
    embedding_provider_scope: str = "codecompass_vector"
    secret_refs: list[str] = Field(default_factory=list)

    artifact_first: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model_provider_ref")
    @classmethod
    def _no_plain_secrets(cls, v: str) -> str:
        if any(ch in v for ch in ("sk-", "Bearer ", "key=", "token=")):
            raise ValueError("model_provider_ref must be a reference, not a secret value")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def _positive_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("timeout_seconds must be positive")
        return v

    @field_validator("max_iterations")
    @classmethod
    def _positive_iterations(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_iterations must be positive")
        return v

    @field_validator("max_nodes")
    @classmethod
    def _positive_nodes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_nodes must be positive")
        return v

    @model_validator(mode="after")
    def _cloud_requires_external_calls(self) -> "LangGraphProviderConfig":
        if self.mode == "cloud_gated" and not self.external_calls_allowed:
            raise ValueError(
                "cloud_gated mode requires external_calls_allowed=true"
            )
        return self

    def requires_human_approval(self, action: str) -> bool:
        return action.lower() in [h.lower() for h in self.human_in_loop_required_for]

    def is_live(self) -> bool:
        return self.enabled and self.mode in ("local_live", "cloud_gated")

    def is_dry_run(self) -> bool:
        return self.mode in ("dry_run", "mock_live") or not self.enabled

    def graph_allowed(self, graph_id: str) -> bool:
        if not self.allowed_graphs:
            return True   # empty list = allow any
        return graph_id in self.allowed_graphs

    @classmethod
    def default_off(cls) -> "LangGraphProviderConfig":
        return cls(enabled=False, mode="dry_run")

    def to_safe_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d.pop("secret_refs", None)
        return d
