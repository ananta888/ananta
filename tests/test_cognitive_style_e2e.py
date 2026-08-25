"""Controlled Hub-domain E2E for complementary cognitive-style routing."""

from __future__ import annotations

from datetime import datetime, timezone

from agent.services.cognitive_style_evidence_service import (
    ComplementaryStyleExperimentService,
)
from agent.services.cognitive_style_overlay_service import CognitiveStyleOverlayService
from agent.services.cognitive_style_service import (
    CognitiveStyleRankingPolicy,
    CognitiveStyleService,
    InMemoryCognitiveStyleStateRepository,
)
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext
from ananta_contracts.cognitive_style import (
    ComplementaryStyleExperimentCommand,
    StyleExperimentMetrics,
)
from ananta_contracts.model_selection import AgentStyleProfile, CognitiveStyleVector


class _Observer:
    def record(self, outcome: str) -> None:
        del outcome


def _style(
    model_profile_id: str,
    scores: tuple[float, float, float],
) -> AgentStyleProfile:
    return AgentStyleProfile(
        profile_id=f"style-{model_profile_id}",
        model_profile_id=model_profile_id,
        scores=CognitiveStyleVector(
            rule_correctness=scores[0],
            truth_exploration=scores[1],
            initiative_assertiveness=scores[2],
        ),
        confidence=.95,
        sample_count=96,
        benchmark_revision="behavior-style-v1",
        measured_at=datetime.now(timezone.utc).isoformat(),
        source="measured",
        model_revision="controlled-r1",
        quantization="q8",
        runtime="llamacpp",
        backend_id="lmstudio",
        prompt_digest="sha256:controlled-system",
        role_prompt_digest="sha256:controlled-role",
        tool_mode="native_tools",
        sampling_digest="sha256:controlled-sampling",
        evidence_refs=(f"style-observation://{model_profile_id}",),
    )


def _local(profile_id: str) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id="lmstudio",
        model=profile_id,
        model_role="any",
        local=True,
        cloud=False,
        supports_tools=True,
        tool_calling_mode="native_tools",
    )


def test_hub_routes_complementary_roles_after_hard_gates_and_compares_control():
    styles = CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    profiles = (
        _local("rule-implementer"),
        _local("exploratory-reviewer"),
        _local("bounded-challenger"),
        ModelProfile(
            profile_id="cloud-stylish-reviewer",
            provider_id="openrouter",
            model="cloud-reviewer",
            model_role="any",
            local=False,
            cloud=True,
            cloud_allowed=True,
            block_secret_context=True,
            supports_tools=True,
            tool_calling_mode="native_tools",
        ),
    )
    measurements = (
        _style("rule-implementer", (.98, .45, .45)),
        _style("exploratory-reviewer", (.82, .97, .55)),
        _style("bounded-challenger", (.6, .92, .98)),
        _style("cloud-stylish-reviewer", (.9, 1, .6)),
    )
    ranking = CognitiveStyleRankingPolicy(
        profiles=measurements,
        targets=styles.read().configuration.role_targets,
        weight=1,
        observer=_Observer(),
    )
    resolver = ModelProfileResolver(list(profiles), style_ranking=ranking)

    role_results = {
        role: resolver.resolve(RoutingContext(
            model_role=role,
            requires_tools=True,
            allow_cloud=True,
            context_text="api_key=abcdefghijklmnopqrstuvwxyz123456",
            metadata={"style_role_id": role},
        ))
        for role in ("implementer", "reviewer", "challenger")
    }

    assert {
        role: result.profile.profile_id for role, result in role_results.items()
    } == {
        "implementer": "rule-implementer",
        "reviewer": "exploratory-reviewer",
        "challenger": "bounded-challenger",
    }
    assert all(
        any(item.source == "cognitive_style_soft_ranking" for item in result.decisions)
        for result in role_results.values()
    )
    assert all(
        any(
            item.profile_id == "cloud-stylish-reviewer"
            and "secrets_detected_cloud_blocked" in item.reason
            for item in result.decisions
        )
        for result in role_results.values()
    )

    overlays = CognitiveStyleOverlayService(styles)
    for role in ("implementer", "reviewer", "challenger"):
        applied = overlays.apply(task={"role_id": role}, system_prompt="Task")
        assert applied.applied is True
        assert applied.permission_delta == "none"
        assert "verändert keine Rechte" in applied.rendered_system_prompt

    capability_only = ModelProfileResolver(list(profiles[:-1]))
    control_profiles = {
        capability_only.resolve(RoutingContext(
            model_role=role, requires_tools=True, metadata={"style_role_id": role},
        )).profile.profile_id
        for role in ("implementer", "reviewer", "challenger")
    }
    assert control_profiles == {"rule-implementer"}

    report = ComplementaryStyleExperimentService().evaluate(
        ComplementaryStyleExperimentCommand(
            experiment_id="controlled-complementary-routing",
            complementary=StyleExperimentMetrics(
                quality_score=.9,
                rework_count=1,
                cost_units=3,
                duration_seconds=3,
                gates_passed=3,
                gates_total=3,
            ),
            homogeneous_control=StyleExperimentMetrics(
                quality_score=.6,
                rework_count=2,
                cost_units=3,
                duration_seconds=3,
                gates_passed=2,
                gates_total=3,
            ),
        )
    )
    assert report.outcome == "supported"
    assert report.security_or_capability_gate_bypassed is False


def test_complementary_routing_hypothesis_remains_falsifiable():
    report = ComplementaryStyleExperimentService().evaluate(
        ComplementaryStyleExperimentCommand(
            experiment_id="controlled-negative-result",
            complementary=StyleExperimentMetrics(
                quality_score=.5, rework_count=3, cost_units=4,
                duration_seconds=5, gates_passed=1, gates_total=3,
            ),
            homogeneous_control=StyleExperimentMetrics(
                quality_score=.8, rework_count=1, cost_units=3,
                duration_seconds=3, gates_passed=3, gates_total=3,
            ),
        )
    )

    assert report.outcome == "falsified"
