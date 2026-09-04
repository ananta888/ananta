"""Closed contracts for local model resource and runtime evaluation profiles."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LOCAL_MODEL_RUNTIME_PROFILE_SCHEMA = "ananta.local-model-runtime-profile.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class RuntimeContextCandidate(_ClosedModel):
    context_tokens: int = Field(ge=512, le=1_000_000)
    estimated_vram_bytes: int = Field(ge=0, le=1024 * 1024**3)
    estimated_ram_bytes: int = Field(ge=0, le=1024 * 1024**3)
    state: Literal["candidate", "stress_only", "verified"] = "candidate"


class RuntimeRequirement(_ClosedModel):
    runtime_id: Literal["ollama", "lmstudio", "llamacpp", "vllm", "sglang"]
    protocol: Literal["ollama_native", "openai_compatible"]
    minimum_version: str | None = Field(default=None, max_length=64)
    reasoning_parser: str | None = Field(default=None, max_length=64)
    tool_parser: str | None = Field(default=None, max_length=64)
    remote_code_allowed: Literal[False] = False
    state: Literal["candidate", "incompatible", "optional"] = "candidate"
    reason_codes: tuple[str, ...] = ()


class LocalModelRuntimeProfile(_ClosedModel):
    schema_version: Literal["ananta.local-model-runtime-profile.v1"] = Field(
        default=LOCAL_MODEL_RUNTIME_PROFILE_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    profile_id: str
    model_profile_id: str
    variant_id: str
    artifact_id: str
    artifact_sha256: str
    artifact_size_bytes: int = Field(ge=1, le=1024 * 1024**3)
    vision_projection_artifact_id: str | None = None
    vision_projection_size_bytes: int = Field(default=0, ge=0, le=1024 * 1024**3)
    hardware_class: str
    gpu_name: str
    minimum_total_vram_bytes: int = Field(ge=0)
    minimum_total_ram_bytes: int = Field(ge=0)
    minimum_reserve_fraction: float = Field(ge=0.15, le=0.5)
    maximum_parallel_requests: int = Field(ge=1, le=64)
    requires_no_swap_growth: Literal[True] = True
    default_context_tokens: int
    contexts: tuple[RuntimeContextCandidate, ...]
    runtimes: tuple[RuntimeRequirement, ...]
    production_default_allowed: Literal[False] = False
    hardware_results_state: Literal["not_run", "unverified", "verified", "failed"] = "not_run"

    @field_validator("profile_id", "model_profile_id", "variant_id", "artifact_id", "hardware_class")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("local_model_profile_identifier_invalid")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("local_model_profile_digest_invalid")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "LocalModelRuntimeProfile":
        tokens = [item.context_tokens for item in self.contexts]
        runtimes = [item.runtime_id for item in self.runtimes]
        if (
            not tokens
            or self.default_context_tokens not in tokens
            or len(tokens) != len(set(tokens))
            or not runtimes
            or len(runtimes) != len(set(runtimes))
        ):
            raise ValueError("local_model_runtime_profile_inconsistent")
        return self


__all__ = [
    "LOCAL_MODEL_RUNTIME_PROFILE_SCHEMA",
    "LocalModelRuntimeProfile",
    "RuntimeContextCandidate",
    "RuntimeRequirement",
]
