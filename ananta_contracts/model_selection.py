"""Closed contracts for Hub-owned model selection and cognitive style."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ananta_contracts.model_catalog import ModelInventorySourceStatus

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")

FallbackTrigger = Literal[
    "provider_unavailable", "connection_error", "timeout", "http_5xx",
    "server_error", "invalid_json_response", "empty_content",
    "schema_validation_failed", "tool_not_allowed", "tool_args_invalid",
    "repeated_tool_failure", "context_too_large",
]


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
    default_model_role: str = "any"
    legacy_config_paths: tuple[str, ...] = ()
    mutation_capability: str = "model_routing.mutate"
    registration_source: str = "builtin"
    non_routable_reason: str | None = None

    @field_validator("consumer_id", "category")
    @classmethod
    def identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("model_consumer_identifier_invalid")
        return value

    @model_validator(mode="after")
    def routing_boundary(self) -> "ModelConsumer":
        if self.routable and self.non_routable_reason:
            raise ValueError("model_consumer_routable_reason_unexpected")
        if not self.routable and (
            not self.non_routable_reason or self.allowed_scopes
        ):
            raise ValueError("model_consumer_non_routable_boundary_invalid")
        return self


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
    triggers: tuple[FallbackTrigger, ...] = ()
    max_context_tokens: int | None = Field(default=None, ge=1)
    max_estimated_cost_per_step: float | None = Field(default=None, ge=0)
    requires_tools: bool = False
    requires_json: bool = False
    cloud_allowed: bool = False


class ModelFallbackGroup(_Closed):
    group_id: str
    candidates: tuple[ModelFallbackCandidate, ...] = Field(min_length=1, max_length=32)
    stop_on_policy_block: bool = True
    max_total_retries: int = Field(default=0, ge=0, le=64)
    on_exhausted: Literal["stop", "escalate"] = "stop"
    escalation_profile_id: str | None = None

    @model_validator(mode="after")
    def unique_candidates(self) -> "ModelFallbackGroup":
        ids = [item.profile_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("model_fallback_duplicate_candidate")
        if not self.stop_on_policy_block:
            raise ValueError("model_fallback_policy_block_must_be_terminal")
        if self.on_exhausted == "escalate" and not self.escalation_profile_id:
            raise ValueError("model_fallback_escalation_profile_required")
        if self.on_exhausted == "stop" and self.escalation_profile_id:
            raise ValueError("model_fallback_escalation_profile_unexpected")
        if self.escalation_profile_id in set(ids):
            raise ValueError("model_fallback_escalation_cycle")
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
    configuration: ModelRoutingConfiguration | None = None

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
    maximum_total_retries: int | None = Field(default=None, ge=0, le=64)
    executable: bool


class EffectiveModelRoutingProjection(_Closed):
    schema_version: Literal["ananta.effective-model-routing-projection.v1"] = Field(
        default="ananta.effective-model-routing-projection.v1", alias="schema"
    )
    configuration_revision: int = Field(ge=0)
    routes: tuple[EffectiveModelRoute, ...] = ()


class ModelRoutingValidationIssue(_Closed):
    severity: Literal["warning", "error"]
    reason_code: str
    reference: str | None = None


class ModelRoutingValidationReport(_Closed):
    schema_version: Literal["ananta.model-routing-validation-report.v1"] = Field(
        default="ananta.model-routing-validation-report.v1", alias="schema"
    )
    valid: bool
    expected_revision: int = Field(ge=0)
    current_revision: int = Field(ge=0)
    issues: tuple[ModelRoutingValidationIssue, ...] = ()


class ModelRoutingExportBundle(_Closed):
    schema_version: Literal["ananta.model-routing-export.v1"] = Field(
        default="ananta.model-routing-export.v1", alias="schema"
    )
    configuration: ModelRoutingConfiguration


class ModelRoutingImportCommand(_Closed):
    schema_version: Literal["ananta.model-routing-import-command.v1"] = Field(
        default="ananta.model-routing-import-command.v1", alias="schema"
    )
    expected_revision: int = Field(ge=0)
    configuration: ModelRoutingConfiguration
    confirmation_digest: str | None = Field(
        default=None, pattern=r"^sha256:[a-f0-9]{64}$"
    )


class ModelRoutingDiff(_Closed):
    added_assignment_keys: tuple[str, ...] = ()
    changed_assignment_keys: tuple[str, ...] = ()
    removed_assignment_keys: tuple[str, ...] = ()
    added_fallback_group_ids: tuple[str, ...] = ()
    changed_fallback_group_ids: tuple[str, ...] = ()
    removed_fallback_group_ids: tuple[str, ...] = ()


class ModelRoutingImportPreview(_Closed):
    schema_version: Literal["ananta.model-routing-import-preview.v1"] = Field(
        default="ananta.model-routing-import-preview.v1", alias="schema"
    )
    current_revision: int = Field(ge=0)
    source_revision: int = Field(ge=0)
    applicable: bool
    confirmation_digest: str
    diff: ModelRoutingDiff
    issues: tuple[ModelRoutingValidationIssue, ...] = ()


class ModelRoutingTemplate(_Closed):
    schema_version: Literal["ananta.model-routing-template.v1"] = Field(
        default="ananta.model-routing-template.v1", alias="schema"
    )
    template_id: Literal[
        "local-only", "local-first-cloud-fallback", "cloud-only", "cli-first"
    ]
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    applicable: bool
    configuration: ModelRoutingConfiguration
    issues: tuple[ModelRoutingValidationIssue, ...] = ()


class ModelRoutingTemplateCatalog(_Closed):
    schema_version: Literal["ananta.model-routing-template-catalog.v1"] = Field(
        default="ananta.model-routing-template-catalog.v1", alias="schema"
    )
    configuration_revision: int = Field(ge=0)
    templates: tuple[ModelRoutingTemplate, ...]


class LegacyModelMigrationEntry(_Closed):
    consumer_id: str
    legacy_source: str
    legacy_provider_id: str | None = None
    legacy_model_id: str | None = None
    matched_profile_id: str | None = None
    status: Literal[
        "missing", "incomplete", "unresolved", "ambiguous", "proposed", "preserved"
    ]
    reason_code: str


class ModelRoutingLegacyMigrationPreview(_Closed):
    schema_version: Literal["ananta.model-routing-legacy-migration-preview.v1"] = Field(
        default="ananta.model-routing-legacy-migration-preview.v1", alias="schema"
    )
    current_revision: int = Field(ge=0)
    applicable: bool
    idempotent: bool = True
    confirmation_digest: str
    entries: tuple[LegacyModelMigrationEntry, ...]
    proposed_configuration: ModelRoutingConfiguration
    issues: tuple[ModelRoutingValidationIssue, ...] = ()


class ModelRoutingLegacyMigrationApplyCommand(_Closed):
    schema_version: Literal["ananta.model-routing-legacy-migration-apply-command.v1"] = Field(
        default="ananta.model-routing-legacy-migration-apply-command.v1", alias="schema"
    )
    expected_revision: int = Field(ge=0)
    confirmation_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class ModelRoutingShadowEntry(_Closed):
    consumer_id: str
    legacy_provider_id: str | None = None
    legacy_model_id: str | None = None
    central_provider_id: str | None = None
    central_model_id: str | None = None
    central_assignment_source: str | None = None
    status: Literal[
        "legacy_missing", "central_missing", "central_disabled", "match", "mismatch",
        "central_profile_unknown",
    ]
    matches: bool | None = None


class ModelRoutingShadowReport(_Closed):
    schema_version: Literal["ananta.model-routing-shadow-report.v1"] = Field(
        default="ananta.model-routing-shadow-report.v1", alias="schema"
    )
    configuration_revision: int = Field(ge=0)
    matches: bool
    entries: tuple[ModelRoutingShadowEntry, ...]


class ModelRoutingReleaseGateCheck(_Closed):
    check_id: str
    passed: bool
    reason_code: str


class ModelRoutingReleaseGateReport(_Closed):
    schema_version: Literal["ananta.model-routing-release-gate.v1"] = Field(
        default="ananta.model-routing-release-gate.v1", alias="schema"
    )
    configuration_revision: int = Field(ge=0)
    ready: bool
    checks: tuple[ModelRoutingReleaseGateCheck, ...]


class ModelRoutingUsageAggregate(_Closed):
    consumer_id: str
    profile_id: str
    selections_total: int = Field(ge=0)
    fallback_selections_total: int = Field(ge=0)
    last_used_at: str


class ModelRoutingDiagnosticIssue(_Closed):
    severity: Literal["warning", "error"]
    reason_code: str
    reference: str | None = None


class ModelRoutingDiagnostics(_Closed):
    schema_version: Literal["ananta.model-routing-diagnostics.v1"] = Field(
        default="ananta.model-routing-diagnostics.v1", alias="schema"
    )
    generated_at: str
    configuration_revision: int = Field(ge=0)
    catalog_revision: int = Field(ge=1)
    assignment_count: int = Field(ge=0)
    fallback_group_count: int = Field(ge=0)
    routable_consumer_count: int = Field(ge=0)
    unresolved_assignment_count: int = Field(ge=0)
    non_executable_route_count: int = Field(ge=0)
    source_statuses: tuple[ModelInventorySourceStatus, ...] = ()
    issues: tuple[ModelRoutingDiagnosticIssue, ...] = ()
    usage: tuple[ModelRoutingUsageAggregate, ...] = ()
    contains_secrets: Literal[False] = False


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
    backend_id: str | None = None
    role_prompt_digest: str | None = None
    evidence_refs: tuple[str, ...] = ()
    expires_at: str | None = None
    additional_dimensions: dict[str, float] = Field(default_factory=dict)

    @field_validator("additional_dimensions")
    @classmethod
    def valid_additional_dimensions(cls, value: dict[str, float]) -> dict[str, float]:
        for name, score in value.items():
            if not _IDENTIFIER.fullmatch(name) or not 0 <= float(score) <= 1:
                raise ValueError("style_additional_dimension_invalid")
        return value

    @model_validator(mode="after")
    def measured_evidence_required(self) -> "AgentStyleProfile":
        if self.source == "measured" and not self.evidence_refs:
            raise ValueError("style_measured_evidence_required")
        return self


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
    must_have: dict[str, StyleRange] = Field(default_factory=dict)
    avoid_ranges: dict[str, tuple[StyleRange, ...]] = Field(default_factory=dict)
    organization_id: str | None = None
    project_id: str | None = None
    overlay_id: str | None = None
    rationale: str = Field(default="", max_length=1000)

    @field_validator("must_have", "avoid_ranges")
    @classmethod
    def known_style_dimensions(cls, value):
        known = {
            "rule_correctness", "truth_exploration", "initiative_assertiveness"
        }
        if any(name not in known for name in value):
            raise ValueError("role_style_dimension_unknown")
        return value


__all__ = [
    "AgentStyleProfile", "CognitiveStyleVector", "EffectiveModelRoute",
    "EffectiveModelRoutingProjection", "ModelAssignment",
    "ModelConsumer", "ModelFallbackCandidate", "ModelFallbackGroup",
    "ModelRouteDecision", "ModelRoutingConfiguration", "ModelRoutingDiff",
    "ModelRoutingDryRunCommand", "ModelRoutingExportBundle",
    "ModelRoutingImportCommand", "ModelRoutingImportPreview",
    "ModelRoutingMutationCommand", "ModelRoutingValidationIssue",
    "ModelRoutingValidationReport", "ModelRoutingTemplate",
    "ModelRoutingTemplateCatalog",
    "LegacyModelMigrationEntry", "ModelRoutingLegacyMigrationApplyCommand",
    "ModelRoutingLegacyMigrationPreview", "ModelRoutingReleaseGateCheck",
    "ModelRoutingReleaseGateReport", "ModelRoutingShadowEntry",
    "ModelRoutingShadowReport",
    "ModelRoutingDiagnosticIssue", "ModelRoutingDiagnostics",
    "ModelRoutingUsageAggregate",
    "RoleStyleTarget", "StyleRange",
]
