from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.models.organization_models import OrganizationCompileRequest
from agent.services.organization_definition_catalog_service import (
    OrganizationDefinitionCatalogService,
)
from tests.organization_support import organization_compiler

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_small_matrix_uses_only_injected_two_and_three_team_fixtures() -> None:
    fixtures = json.loads(
        (ROOT / "tests/fixtures/scenarios/organization-small-compositions.json").read_text(encoding="utf-8")
    )
    catalog = OrganizationDefinitionCatalogService(repository_root=ROOT)

    assert catalog.production_payload()["test_only_fixtures"] == []
    assert fixtures["production_seed_allowed"] is False
    assert len(fixtures["fixtures"]) == 4

    for fixture in fixtures["fixtures"]:
        plan = organization_compiler().compile(
            OrganizationCompileRequest(
                tenant_id="tenant-small-fixture",
                project_id="project-small-fixture",
                organization_id=f"organization-{fixture['key']}",
                definition_ref="enterprise_scrum_organization@1",
                composition_mode="custom",
                custom_composition=fixture["team_blueprint_counts"],
                admission_exception_ref="test-only-small@1",
            )
        )

        assert plan.requested_team_count == fixture["expected_total_team_count"]
        assert plan.expected_counts["contains"] == fixture["expected_contains_edges"]
        assert plan.expected_counts["organization_relation"] == fixture["expected_organization_edges"]
        assert sorted(plan.capability_gaps) == sorted(fixture["expected_diagnostic_codes"])
        assert not plan.blockers


@pytest.mark.integration
def test_small_fixture_without_fresh_admission_exception_fails_closed() -> None:
    with pytest.raises(ValidationError, match="custom_composition_shape_invalid"):
        OrganizationCompileRequest(
            tenant_id="tenant-small-fixture",
            project_id="project-small-fixture",
            organization_id="organization-without-admission",
            definition_ref="enterprise_scrum_organization@1",
            composition_mode="custom",
            custom_composition={"enterprise_product_delivery_scrum": 2},
        )
