from __future__ import annotations

from types import SimpleNamespace

from agent.services.business_controlling_scientific_gate import (
    ScientificPilotStatisticalCapabilityGate,
)
from agent.services.scientific_skill_pilot_service import ScientificSkillPilotCard
from agent.services.source_control_access_policy import (
    HubSourcePrincipal,
    SourceObjectBinding,
)


class _Pilot:
    def __init__(self, cards: tuple[ScientificSkillPilotCard, ...]) -> None:
        self.cards = cards

    def available(self, **_: object) -> tuple[ScientificSkillPilotCard, ...]:
        return self.cards


def _card(*, mode: str = "controlled-execution") -> ScientificSkillPilotCard:
    return ScientificSkillPilotCard(
        entry_id="skillentry_" + "a" * 64,
        skill_name="statsmodels",
        upstream_repository="https://github.com/K-Dense-AI/scientific-agent-skills",
        upstream_path="scientific_skills/statsmodels/SKILL.md",
        upstream_pin="b" * 40,
        skill_sha256="c" * 64,
        allowed_mode=mode,
        context_budget_tokens=1000,
        allowed_tools=(),
        network_profile="denied",
        approval_level="task",
        approval_status="approved",
        source_reference="bound-reference",
    )


def _gate(card: ScientificSkillPilotCard) -> ScientificPilotStatisticalCapabilityGate:
    return ScientificPilotStatisticalCapabilityGate(
        pilot=_Pilot((card,)),  # type: ignore[arg-type]
        catalog=SimpleNamespace(catalog_digest="d" * 64),  # type: ignore[arg-type]
        principal=HubSourcePrincipal(
            "actor-a",
            "tenant-a",
            "project-a",
            frozenset({"scientific_skill_pilot"}),
        ),
        binding=SourceObjectBinding("source-a", "tenant-a", "project-a"),
    )


def test_gate_requires_scoped_offline_controlled_execution_card() -> None:
    decision = _gate(_card()).assess(
        tenant_id="tenant-a",
        project_id="project-a",
        skill_name="statsmodels",
        catalog_entry_id="skillentry_" + "a" * 64,
    )

    assert decision.admitted is True
    assert decision.local_execution is True
    assert decision.network_allowed is False
    assert len(decision.capability_digest) == 64


def test_documentation_only_or_cross_scope_card_is_denied() -> None:
    documentation_only = _gate(_card(mode="documentation-only")).assess(
        tenant_id="tenant-a",
        project_id="project-a",
        skill_name="statsmodels",
        catalog_entry_id="skillentry_" + "a" * 64,
    )
    cross_scope = _gate(_card()).assess(
        tenant_id="tenant-other",
        project_id="project-a",
        skill_name="statsmodels",
        catalog_entry_id="skillentry_" + "a" * 64,
    )

    assert documentation_only.admitted is False
    assert cross_scope.reason_code == "controlling_statistical_scope_denied"
