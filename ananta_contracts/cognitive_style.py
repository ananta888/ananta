"""Closed contracts for cognitive-style measurement and governance."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ananta_contracts.model_selection import AgentStyleProfile, RoleStyleTarget


class _Closed(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=True, populate_by_name=True
    )


StyleDimension = Literal[
    "rule_correctness", "truth_exploration", "initiative_assertiveness"
]


class StyleMeasurementContext(_Closed):
    model_profile_id: str
    model_revision: str
    quantization: str
    runtime: str
    backend_id: str
    system_prompt_digest: str
    role_prompt_digest: str
    tool_mode: str
    sampling_digest: str


class StyleBenchmarkVariant(_Closed):
    case_id: str
    variant_id: str
    dimension: StyleDimension
    prompt: str = Field(min_length=10, max_length=4000)
    positive_markers: tuple[str, ...] = Field(min_length=1)
    negative_markers: tuple[str, ...] = ()
    safety_refusal_markers: tuple[str, ...] = ()


class StyleBenchmarkPlan(_Closed):
    schema_version: Literal["ananta.style-benchmark-plan.v1"] = Field(
        default="ananta.style-benchmark-plan.v1", alias="schema"
    )
    benchmark_revision: str
    context: StyleMeasurementContext
    variants: tuple[StyleBenchmarkVariant, ...] = Field(min_length=6, max_length=60)
    repeats: int = Field(default=2, ge=2, le=5)
    seeds: tuple[int, ...] = Field(default=(17, 41), min_length=2, max_length=5)
    temperatures: tuple[float, ...] = Field(default=(0.0, 0.4), min_length=2, max_length=4)

    @field_validator("temperatures")
    @classmethod
    def temperatures_in_range(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not 0 <= item <= 2 for item in value):
            raise ValueError("style_benchmark_temperature_invalid")
        return value


class StyleBenchmarkObservation(_Closed):
    observation_id: str
    case_id: str
    variant_id: str
    dimension: StyleDimension
    repeat_index: int = Field(ge=0)
    seed: int
    temperature: float = Field(ge=0, le=2)
    score: float = Field(ge=0, le=1)
    refused_for_safety: bool = False
    prompt_sensitivity_group: str
    evidence_ref: str
    output_digest: str


class StyleBenchmarkResult(_Closed):
    schema_version: Literal["ananta.style-benchmark-result.v1"] = Field(
        default="ananta.style-benchmark-result.v1", alias="schema"
    )
    profile: AgentStyleProfile
    observations: tuple[StyleBenchmarkObservation, ...]
    prompt_sensitivity: dict[StyleDimension, float]
    judge_used: bool = False
    judge_calibration_ref: str | None = None

    @model_validator(mode="after")
    def judge_requires_calibration(self) -> "StyleBenchmarkResult":
        if self.judge_used and not self.judge_calibration_ref:
            raise ValueError("style_benchmark_judge_calibration_required")
        return self


class RoleStyleOverlay(_Closed):
    overlay_id: str
    role_id: str
    revision: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=4000)
    reinforces: tuple[StyleDimension, ...] = ()
    permission_delta: Literal["none"] = "none"
    enabled: bool = True


class CognitiveStyleConfiguration(_Closed):
    schema_version: Literal["ananta.cognitive-style-configuration.v1"] = Field(
        default="ananta.cognitive-style-configuration.v1", alias="schema"
    )
    revision: int = Field(ge=0)
    profiles: tuple[AgentStyleProfile, ...] = ()
    role_targets: tuple[RoleStyleTarget, ...] = ()
    overlays: tuple[RoleStyleOverlay, ...] = ()

    @model_validator(mode="after")
    def unique_active_keys(self) -> "CognitiveStyleConfiguration":
        profile_ids = [item.profile_id for item in self.profiles]
        target_keys = [
            (item.target_id, item.organization_id, item.project_id)
            for item in self.role_targets
        ]
        overlay_ids = [item.overlay_id for item in self.overlays]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("style_profile_duplicate")
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("role_style_target_duplicate")
        if len(overlay_ids) != len(set(overlay_ids)):
            raise ValueError("role_style_overlay_duplicate")
        return self


class CognitiveStyleMutationCommand(_Closed):
    schema_version: Literal["ananta.cognitive-style-mutation-command.v1"] = Field(
        default="ananta.cognitive-style-mutation-command.v1", alias="schema"
    )
    expected_revision: int = Field(ge=0)
    profiles: tuple[AgentStyleProfile, ...] = ()
    role_targets: tuple[RoleStyleTarget, ...] = ()
    overlays: tuple[RoleStyleOverlay, ...] = ()


class TeamStyleMember(_Closed):
    agent_id: str
    role_id: str
    model_profile_id: str


class TeamStyleDiversityReport(_Closed):
    schema_version: Literal["ananta.team-style-diversity.v1"] = Field(
        default="ananta.team-style-diversity.v1", alias="schema"
    )
    members_evaluated: int = Field(ge=0)
    centroid: dict[StyleDimension, float]
    spread: dict[StyleDimension, float]
    classification: Literal[
        "insufficient_data", "rule_oriented", "exploratory", "initiative_oriented",
        "balanced", "homogeneous",
    ]
    warnings: tuple[str, ...] = ()
    complementary_role_ids: tuple[str, ...] = ()
    capability_or_security_overridden: bool = False


class StyleMismatchEvidence(_Closed):
    evidence_id: str
    agent_id: str
    role_id: str
    model_profile_id: str
    signal: Literal[
        "rework", "overthinking", "rule_violation", "missing_initiative", "scope_expansion"
    ]
    observed_at: str
    correlation_score: float = Field(ge=-1, le=1)
    hypothesis: str = Field(min_length=1, max_length=1000)
    alternative_causes: tuple[str, ...] = Field(min_length=1)
    causes_reclassification: Literal[False] = False


class StyleEvolutionProposal(_Closed):
    proposal_id: str
    proposal_type: Literal["style_target", "role_overlay", "model_routing"]
    status: Literal[
        "proposed", "validated", "approved", "applied", "measuring", "rolled_back", "rejected"
    ] = "proposed"
    hypothesis: str = Field(min_length=1, max_length=2000)
    expected_effect: str = Field(min_length=1, max_length=1000)
    experiment_id: str | None = None
    sprint_id: str | None = None
    payload: dict[str, object]
    evidence_refs: tuple[str, ...] = ()
    review_required: Literal[True] = True
    rollback_payload: dict[str, object] = Field(default_factory=dict)


class StyleEvolutionTransitionCommand(_Closed):
    schema_version: Literal["ananta.style-evolution-transition-command.v1"] = Field(
        default="ananta.style-evolution-transition-command.v1", alias="schema"
    )
    expected_status: str
    target_status: Literal[
        "validated", "approved", "applied", "measuring", "rolled_back", "rejected"
    ]
    review_reference: str | None = None


class CognitiveStyleReadModel(_Closed):
    schema_version: Literal["ananta.cognitive-style-read-model.v1"] = Field(
        default="ananta.cognitive-style-read-model.v1", alias="schema"
    )
    configuration: CognitiveStyleConfiguration
    profile_history: tuple[AgentStyleProfile, ...] = ()
    mismatch_evidence: tuple[StyleMismatchEvidence, ...] = ()
    evolution_proposals: tuple[StyleEvolutionProposal, ...] = ()
    heuristic_notice: str


class CognitiveStylePersistedState(_Closed):
    schema_version: Literal["ananta.cognitive-style-state.v1"] = Field(
        default="ananta.cognitive-style-state.v1", alias="schema"
    )
    configuration: CognitiveStyleConfiguration
    profile_history: tuple[AgentStyleProfile, ...] = ()
    mismatch_evidence: tuple[StyleMismatchEvidence, ...] = ()
    evolution_proposals: tuple[StyleEvolutionProposal, ...] = ()


__all__ = [
    "CognitiveStyleConfiguration", "CognitiveStyleMutationCommand",
    "CognitiveStylePersistedState", "CognitiveStyleReadModel", "RoleStyleOverlay", "StyleBenchmarkObservation",
    "StyleBenchmarkPlan", "StyleBenchmarkResult", "StyleBenchmarkVariant",
    "StyleEvolutionProposal", "StyleEvolutionTransitionCommand",
    "StyleMeasurementContext", "StyleMismatchEvidence", "StyleDimension",
    "TeamStyleDiversityReport", "TeamStyleMember",
]
