from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.models.organization_models import OrganizationCompileRequest, TeamCountRange
from agent.services.organization_blueprint_compiler import OrganizationBlueprintCompiler
from agent.services.organization_compile_application_service import (
    DenyOrganizationAdmissionPolicy,
    OrganizationCompileApplicationService,
)
from agent.services.organization_definition_catalog_service import (
    OrganizationDefinitionCatalogService,
)

ROOT = Path(__file__).resolve().parents[1]
LEAN_MATRIX = {
    2: (5, {"lean_company_direction": 1, "lean_delivery_cell": 1}),
    3: (8, {"lean_company_direction": 1, "lean_delivery_cell": 1, "lean_discovery": 1}),
    4: (
        12,
        {
            "lean_company_direction": 1,
            "lean_delivery_cell": 1,
            "lean_discovery": 1,
            "lean_enablement": 1,
        },
    ),
    5: (
        16,
        {
            "lean_company_direction": 1,
            "lean_delivery_cell": 2,
            "lean_discovery": 1,
            "lean_enablement": 1,
        },
    ),
    6: (
        20,
        {
            "lean_company_direction": 1,
            "lean_delivery_cell": 3,
            "lean_discovery": 1,
            "lean_enablement": 1,
        },
    ),
}


def _catalog() -> OrganizationDefinitionCatalogService:
    catalog = OrganizationDefinitionCatalogService(repository_root=ROOT)
    catalog.reload()
    return catalog


def _compiler(catalog: OrganizationDefinitionCatalogService) -> OrganizationBlueprintCompiler:
    return OrganizationBlueprintCompiler(
        definitions=catalog,
        limit_profiles=catalog,
        admission_policy=DenyOrganizationAdmissionPolicy(),
    )


def _compile(catalog: OrganizationDefinitionCatalogService, team_count: int):
    return _compiler(catalog).compile(
        OrganizationCompileRequest(
            tenant_id="tenant-lean",
            project_id="project-lean",
            organization_id=f"lean-{team_count}",
            definition_ref="lean_company_organization@1",
            composition_mode="standard",
            team_count=team_count,
        )
    )


@pytest.mark.parametrize(("team_count", "expected"), LEAN_MATRIX.items())
def test_real_catalog_compiles_exact_lean_company_role_matrix(
    team_count: int,
    expected: tuple[int, dict[str, int]],
) -> None:
    catalog = _catalog()
    expected_roles, expected_team_counts = expected

    plan = _compile(catalog, team_count)

    assert plan.requested_team_count == team_count
    assert len(plan.role_slots) == expected_roles
    assert plan.expected_counts["assignment_capacity_default"] == expected_roles
    assert all(slot.default_count == 1 for slot in plan.role_slots)
    assert {
        key.removeprefix("team_blueprint:"): value
        for key, value in plan.expected_counts.items()
        if key.startswith("team_blueprint:")
    } == expected_team_counts


def test_lean_company_contract_keeps_hub_as_only_orchestrator() -> None:
    catalog = _catalog()
    definition = catalog.get_organization_blueprint("lean_company_organization", 1)

    assert definition is not None
    assert definition.orchestration.owner == "hub"
    assert definition.orchestration.workers_may_orchestrate is False
    assert (
        definition.standard_composition.minimum,
        definition.standard_composition.default,
        definition.standard_composition.maximum,
    ) == (2, 4, 6)

    lean_teams = [
        team for (key, _version), team in catalog.snapshot().team_blueprints.items() if key.startswith("lean_")
    ]
    assert len(lean_teams) == 4
    for team in lean_teams:
        assert all(slot.default_count == 1 for slot in team.role_slots)
        assert all("worker_orchestration" in slot.assignment_policy.forbidden_capabilities for slot in team.role_slots)
        workflow_ref = team.workflow_ref.rsplit("@", 1)
        workflow = catalog.get_workflow_definition(workflow_ref[0], int(workflow_ref[1]))
        assert workflow is not None
        assert {step["owner_role_ref"] for step in workflow["steps"]} == {
            slot.role_template_ref for slot in team.role_slots
        }
        for step in workflow["steps"]:
            gate = step["gate"]
            if gate["independent_principal_required"] and gate["approval_role_ref"]:
                assert gate["approval_role_ref"] != step["owner_role_ref"]

    aggregate = catalog.snapshot().aggregate
    handoffs = {row["key"]: row for row in aggregate["handoff_definitions"]}
    assert {
        "lean_direction_goal_handoff",
        "lean_delivery_need_handoff",
        "lean_discovery_evidence_handoff",
        "lean_enablement_readiness_handoff",
    }.issubset(handoffs)
    direction = catalog.get_workflow_definition("lean_company_direction_workflow", 1)
    assert direction is not None
    direction_step = direction["steps"][0]
    assert "accepted_requirements" in direction_step["outputs"]
    assert direction_step["handoff_ref"] == "lean_direction_goal_handoff@1"
    for workflow_key in (
        "lean_company_direction_workflow",
        "lean_delivery_cell_workflow",
        "lean_discovery_workflow",
        "lean_enablement_workflow",
    ):
        workflow = catalog.get_workflow_definition(workflow_key, 1)
        assert workflow is not None
        for step in workflow["steps"]:
            handoff_ref = step.get("handoff_ref")
            if not handoff_ref:
                continue
            handoff_key = handoff_ref.rsplit("@", 1)[0]
            assert set(handoffs[handoff_key]["required_artifact_kinds"]).issubset(step["outputs"])


def test_lean_acceptance_fixtures_bind_role_and_assignment_counts() -> None:
    catalog = _catalog()
    fixtures = {
        row["parameter_overrides"]["team_count"]: row
        for row in catalog.snapshot().aggregate["acceptance_fixtures"]
        if row["organization_blueprint_ref"] == "lean_company_organization@1"
    }

    assert sorted(fixtures) == [2, 3, 4, 5, 6]
    for team_count, (expected_roles, expected_team_counts) in LEAN_MATRIX.items():
        fixture = fixtures[team_count]
        assert fixture["expected_role_slot_count"] == expected_roles
        assert fixture["expected_default_assignment_capacity"] == expected_roles
        assert fixture["expected_team_blueprint_counts"] == expected_team_counts


def test_enterprise_eight_team_capacity_is_unchanged() -> None:
    catalog = _catalog()
    plan = _compiler(catalog).compile(
        OrganizationCompileRequest(
            tenant_id="tenant-enterprise",
            project_id="project-enterprise",
            organization_id="enterprise-eight",
            definition_ref="enterprise_scrum_organization@1",
            composition_mode="standard",
            team_count=8,
        )
    )

    assert plan.requested_team_count == 8
    assert len(plan.role_slots) == 82
    assert plan.expected_counts["assignment_capacity_default"] == 73


def test_team_count_range_is_definition_driven_and_ordered() -> None:
    assert TeamCountRange(minimum=2, default=4, maximum=6).model_dump() == {
        "minimum": 2,
        "default": 4,
        "maximum": 6,
    }
    with pytest.raises(ValidationError, match="standard_team_count_band_invalid"):
        TeamCountRange(minimum=4, default=3, maximum=6)


class _SummaryService(OrganizationCompileApplicationService):
    def __init__(self, catalog: OrganizationDefinitionCatalogService) -> None:
        super().__init__(
            catalog=catalog,
            admission_policy=None,
            signing_secret="lean-company-summary-test-secret",
        )
        self._summary_catalog = catalog

    @contextmanager
    def _scoped_ports(self, _tenant_id: str, _project_id: str):
        yield self._summary_catalog, self._summary_catalog, object()

    def _active_definitions(self, **_kwargs):
        return self._summary_catalog.list_organization_blueprints()


def test_blueprint_summaries_expose_definition_bound_profile_and_capacity() -> None:
    summaries = _SummaryService(_catalog()).list_blueprint_summaries(
        tenant_id="tenant-summary",
        project_id="project-summary",
    )
    lean = {row["team_count"]: row for row in summaries if row["profile_family"] == "lean_company"}

    assert sorted(lean) == [2, 3, 4, 5, 6]
    for team_count, (expected_roles, _expected_teams) in LEAN_MATRIX.items():
        summary = lean[team_count]
        assert summary["profile_label"] == "Lean Company"
        assert summary["role_slot_count"] == expected_roles
        assert summary["default_assignment_capacity"] == expected_roles
        assert summary["size_label"]
        assert summary["supported_team_counts"] == [2, 3, 4, 5, 6]
        assert summary["supported_team_count_min"] == 2
        assert summary["supported_team_count_default"] == 4
        assert summary["supported_team_count_max"] == 6
        defaults = {option["key"]: option["standard_default_count"] for option in summary["custom_team_blueprints"]}
        assert defaults["lean_company_direction"] == 1
        assert defaults["lean_delivery_cell"] == 1
