"""Closed contracts for Hub-owned model selection and cognitive style."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")


class _Closed(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=True, populate_by_name=True
    )


class ModelConsumer(_Closed):
    schema_version: Literal["ananta.model-consumer.v1"] = Field(
        default="ananta.model-consumer.v1", alias="schema"
    )
    consumer_id: str
    label: str = Field(min_length=1, max_length=160)
    category: str
    required_capabilities: tuple[str, ...] = ()
    allowed_scopes: tuple[str, ...] = ("global",)
    routable: bool = True

    @field_validator("consumer_id", "category")
    @classmethod
    def identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_consumer_identifier_invalid")
        return value


class ModelAssignment(_Closed):
    consumer_id: str
    scope: Literal["global", "organization", "project", "workflow", "agent", "role", "task_kind", "step"]
    scope_id: str = "global"
    mode: Literal["inherit", "profile", "model", "disabled"] = "inherit"
    profile_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    fallback_group_id: str | None = None

    @field_validator(
        "consumer_id", "scope_id", "profile_id", "provider_id", "model_id",
        "fallback_group_id",
    )
    @classmethod
    def assignment_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_assignment_identifier_invalid")
        return value

    @model_validator(mode="after")
    def coherent(self) -> "ModelAssignment":
        values = (self.profile_id, self.provider_id, self.model_id)
        if self.mode == "profile" and (not self.profile_id or any(values[1:])):
            raise ValueError("model_assignment_profile_invalid")
        if self.mode == "model" and (not self.provider_id or not self.model_id or self.profile_id):
            raise ValueError("model_assignment_model_invalid")
        if self.mode in {"inherit", "disabled"} and any(values):
            raise ValueError("model_assignment_mode_invalid")
        if self.scope == "global" and self.scope_id != "global":
            raise ValueError("model_assignment_global_scope_id_invalid")
        if self.scope != "global" and self.scope_id == "global":
            raise ValueError("model_assignment_scoped_id_required")
        return self


class ModelFallbackCandidate(_Closed):
    profile_id: str
    retry_budget: int = Field(default=0, ge=0, le=8)
    triggers: tuple[str, ...] = ()
    max_context_tokens: int | None = Field(default=None, ge=1)
    cloud_allowed: bool = False


class ModelFallbackGroup(_Closed):
    group_id: str
    candidates: tuple[ModelFallbackCandidate, ...] = Field(min_length=1, max_length=32)
    stop_on_policy_block: bool = True

    @model_validator(mode="after")
    def unique_candidates(self) -> "ModelFallbackGroup":
        ids = [item.profile_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("model_fallback_duplicate_candidate")
        if not self.stop_on_policy_block:
            raise ValueError("model_fallback_policy_block_must_be_terminal")
        return self


class ModelRoutingConfiguration(_Closed):
    schema_version: Literal["ananta.model-routing-config.v1"] = Field(
        default="ananta.model-routing-config.v1", alias="schema"
    )
    revision: int = Field(ge=0)
    assignments: tuple[ModelAssignment, ...] = ()
    fallback_groups: tuple[ModelFallbackGroup, ...] = ()

    @model_validator(mode="after")
    def unique_keys(self) -> "ModelRoutingConfiguration":
        assignment_keys = [(x.consumer_id, x.scope, x.scope_id) for x in self.assignments]
        groups = [x.group_id for x in self.fallback_groups]
        if len(assignment_keys) != len(set(assignment_keys)):
            raise ValueError("model_assignment_duplicate")
        if len(groups) != len(set(groups)):
            raise ValueError("model_fallback_group_duplicate")
        known_groups = set(groups)
        if any(x.fallback_group_id and x.fallback_group_id not in known_groups for x in self.assignments):
            raise ValueError("model_assignment_fallback_unknown")
        return self


class ModelRoutingMutationCommand(_Closed):
    schema_version: Literal["ananta.model-routing-mutation-command.v1"] = Field(alias="schema")
    expected_revision: int = Field(ge=0)
    assignments: tuple[ModelAssignment, ...] = ()
    fallback_groups: tuple[ModelFallbackGroup, ...] = ()


class ModelRoutingDryRunCommand(_Closed):
    schema_version: Literal["ananta.model-routing-dry-run-command.v1"] = Field(
        default="ananta.model-routing-dry-run-command.v1", alias="schema"
    )
    consumer_id: str
    organization_id: str | None = None
    project_id: str | None = None
    workflow_id: str | None = None
    agent_id: str | None = None
    role_id: str | None = None
    task_kind: str | None = None
    step_id: str | None = None
    risk_class: str | None = None
    data_class: Literal["public", "internal", "confidential", "secret"] = "internal"
    requires_tools: bool = False
    requires_json: bool = False
    requires_streaming: bool = False
    approximate_context_tokens: int = Field(default=0, ge=0, le=1_000_000)
    contains_secrets: bool = False
    allow_cloud: bool = False

    @field_validator(
        "consumer_id", "organization_id", "project_id", "workflow_id",
        "agent_id", "role_id", "task_kind", "step_id", "risk_class",
    )
    @classmethod
    def routing_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_routing_identifier_invalid")
        return value


class ModelRouteDecision(_Closed):
    rank: int = Field(ge=0)
    source: str
    profile_id: str | None = None
    accepted: bool
    reason: str


class EffectiveModelRoute(_Closed):
    schema_version: Literal["ananta.effective-model-route.v1"] = Field(
        default="ananta.effective-model-route.v1", alias="schema"
    )
    configuration_revision: int = Field(ge=0)
    consumer_id: str
    assignment_source: str
    inheritance_sources: tuple[str, ...] = ()
    assignment_mode: Literal["inherit", "profile", "model", "disabled"]
    resolved_profile_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    fallback_group_id: str | None = None
    candidate_profile_ids: tuple[str, ...] = ()
    blocked_candidates: tuple[tuple[str, str], ...] = ()
    decisions: tuple[ModelRouteDecision, ...] = ()
    executable: bool


class CognitiveStyleVector(_Closed):
    rule_correctness: float = Field(ge=0, le=1)
    truth_exploration: float = Field(ge=0, le=1)
    initiative_assertiveness: float = Field(ge=0, le=1)


class AgentStyleProfile(_Closed):
    schema_version: Literal["ananta.agent-style-profile.v1"] = Field(
        default="ananta.agent-style-profile.v1", alias="schema"
    )
    profile_id: str
    model_profile_id: str
    scores: CognitiveStyleVector
    confidence: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=1)
    benchmark_revision: str
    measured_at: str
    source: Literal["measured", "inferred", "configured", "temporary_override"]
    model_revision: str
    quantization: str
    runtime: str
    prompt_digest: str
    tool_mode: str
    sampling_digest: str


class StyleRange(_Closed):
    minimum: float = Field(ge=0, le=1)
    maximum: float = Field(ge=0, le=1)
    weight: float = Field(default=1, ge=0, le=10)

    @model_validator(mode="after")
    def ordered(self) -> "StyleRange":
        if self.minimum > self.maximum:
            raise ValueError("style_range_invalid")
        return self


class RoleStyleTarget(_Closed):
    schema_version: Literal["ananta.role-style-target.v1"] = Field(
        default="ananta.role-style-target.v1", alias="schema"
    )
    target_id: str
    role_id: str
    rule_correctness: StyleRange
    truth_exploration: StyleRange
    initiative_assertiveness: StyleRange


__all__ = [
    "AgentStyleProfile", "CognitiveStyleVector", "EffectiveModelRoute", "ModelAssignment",
    "ModelConsumer", "ModelFallbackCandidate", "ModelFallbackGroup",
    "ModelRouteDecision", "ModelRoutingConfiguration", "ModelRoutingDryRunCommand",
    "ModelRoutingMutationCommand",
    "RoleStyleTarget", "StyleRange",
]
