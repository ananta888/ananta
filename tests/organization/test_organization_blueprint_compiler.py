from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.models.organization_models import OrganizationCompileRequest
from agent.services.organization_blueprint_compiler import OrganizationCompilationError
from tests.organization_support import FakeDefinitionCatalog, organization_compiler


def request(**overrides):
    payload = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "organization_id": "org-a",
        "definition_ref": "enterprise_scrum_organization@1",
        "composition_mode": "standard",
        "team_count": 8,
    }
    payload.update(overrides)
    return OrganizationCompileRequest(**payload)


@pytest.mark.parametrize("team_count", range(5, 11))
def test_standard_band_uses_one_deterministic_expansion_algorithm(team_count):
    plan = organization_compiler().compile(request(team_count=team_count))

    assert plan.requested_team_count == team_count
    assert plan.expected_counts["team"] == team_count
    assert plan.expected_counts["contains"] == team_count + 4
    assert not plan.blockers


def test_medium_reference_materializes_expected_eight_team_mix():
    plan = organization_compiler().compile(request())

    assert plan.expected_counts == {
        "team": 8,
        "unit": 12,
        "role_slot": 8,
        "assignment_capacity_default": 8,
        "workflow_step": 8,
        "organization_relation": 8,
        "contains": 12,
        "team_blueprint:architecture_governance": 1,
        "team_blueprint:enterprise_product_delivery_scrum": 2,
        "team_blueprint:platform_devops_sre": 1,
        "team_blueprint:portfolio_product_coordination": 1,
        "team_blueprint:proof_of_concept": 1,
        "team_blueprint:quality_security_release": 1,
        "team_blueprint:research_and_discovery": 1,
    }


def test_small_custom_composition_requires_exception_and_reports_capability_gaps():
    plan = organization_compiler().compile(
        request(
            composition_mode="custom",
            team_count=None,
            custom_composition={"enterprise_product_delivery_scrum": 2},
            admission_exception_ref="test-only-small@1",
        )
    )

    assert plan.requested_team_count == 2
    assert set(plan.capability_gaps) == {
        "STANDARD_CAPABILITY_GAP_PORTFOLIO",
        "STANDARD_CAPABILITY_GAP_RESEARCH",
        "STANDARD_CAPABILITY_GAP_PLATFORM",
    }


def test_compiler_preserves_stable_group_ids_during_scale_out():
    compiler = organization_compiler()
    five = compiler.compile(request(team_count=5))
    ten = compiler.compile(request(team_count=10))

    five_ids = {unit.unit_key: unit.planned_id for unit in five.units}
    ten_ids = {unit.unit_key: unit.planned_id for unit in ten.units}
    assert all(ten_ids[key] == value for key, value in five_ids.items())


def test_compile_is_read_only_and_rejects_boolean_team_counts():
    catalog = FakeDefinitionCatalog()
    plan = organization_compiler(catalog).compile(request())
    assert catalog.reads > 0
    assert plan.plan_digest

    with pytest.raises(ValidationError):
        request(team_count=True)


def test_custom_singleton_cannot_be_multiplied():
    with pytest.raises(OrganizationCompilationError) as exc:
        organization_compiler().compile(
            request(
                composition_mode="custom",
                team_count=None,
                custom_composition={"platform_devops_sre": 2},
                admission_exception_ref="test-only-small@1",
            )
        )
    assert exc.value.reason_code == "ORGANIZATION_SINGLETON_COUNT_INVALID"
