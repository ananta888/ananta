from __future__ import annotations

import json
from pathlib import Path

from agent.services.organization_definition_catalog_service import (
    OrganizationDefinitionCatalogService,
)
from agent.services.seed_blueprint_catalog import SeedBlueprintCatalog
from agent.services.seed_template_catalog import SeedTemplateCatalog


ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "config/blueprints/standard"


def test_story_domain_template_fragment_loads() -> None:
    catalog = SeedTemplateCatalog()

    assert "Story-Domain-Implementation" in catalog.known_team_types()

    roles = catalog.get_role_specs_for_team_type("Story-Domain-Implementation")
    assert [role["name"] for role in roles] == [
        "Story Analyst",
        "Domain Modeler",
        "Implementation Coder",
        "Verification Tester",
    ]

    templates = catalog.get_templates_for_team_type("Story-Domain-Implementation")
    assert {template["name"] for template in templates} == {
        "Story Domain - Story Analyst",
        "Story Domain - Domain Modeler",
        "Story Domain - Implementation Coder",
        "Story Domain - Verification Tester",
    }
    assert all(
        "Story/domain-first working contract" in template["prompt_template"]
        for template in templates
    )


def test_story_domain_blueprint_fragment_loads() -> None:
    catalog = SeedBlueprintCatalog()
    blueprint = catalog.get_blueprint("Story-Domain-Implementation")

    assert blueprint is not None
    assert blueprint["base_team_type_name"] == "Story-Domain-Implementation"
    assert [role["name"] for role in blueprint["roles"]] == [
        "Story Analyst",
        "Domain Modeler",
        "Implementation Coder",
        "Verification Tester",
    ]

    workflow = blueprint["workflow"]
    assert workflow["mode"] == "gated"
    steps = workflow["steps"]
    assert [step["id"] for step in steps] == [
        "story",
        "domain",
        "implementation",
        "verification",
    ]

    by_id = {step["id"]: step for step in steps}
    assert by_id["story"]["produces"] == [
        "User Story",
        "Acceptance Criteria",
        "Story Constraints",
    ]
    assert by_id["domain"]["depends_on"] == ["story"]
    assert by_id["domain"]["consumes"] == [
        "User Story",
        "Acceptance Criteria",
        "Story Constraints",
    ]
    assert by_id["implementation"]["depends_on"] == ["domain"]
    assert "Domain Model" in by_id["implementation"]["consumes"]
    assert by_id["verification"]["depends_on"] == ["implementation"]
    assert by_id["verification"]["gate"] is True
    assert by_id["verification"]["checks"]["verification_required"] is True


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fragment_assembly_has_unique_versioned_references() -> None:
    snapshot = OrganizationDefinitionCatalogService(repository_root=ROOT).reload()

    sections = (
        snapshot.role_templates,
        snapshot.team_blueprints,
        snapshot.organization_blueprints,
        snapshot.workflows,
        snapshot.handoffs,
        snapshot.policies,
        snapshot.limit_profiles,
    )
    for section in sections:
        assert len(section) == len(set(section))
        assert all(key and version >= 1 for key, version in section)


def test_eight_team_fixture_matches_catalog_fragment() -> None:
    fragment = _json(
        CONFIG_ROOT / "organizations.d/enterprise-scrum-organization.json"
    )
    fixture = _json(
        ROOT / "tests/fixtures/scenarios/enterprise-scrum-medium-eight-team.json"
    )
    source = fragment["acceptance_fixtures"][0]

    assert fixture["fixture_key"] == source["key"]
    assert fixture["parameters"] == source["parameter_overrides"]
    assert fixture["expected"]["team_blueprint_counts"] == source[
        "expected_team_blueprint_counts"
    ]
    assert fixture["expected"]["topology_counts"]["team"] == 8
    assert fixture["expected"]["topology_counts"]["contains_edges"] == 12
    assert fixture["expected"]["topology_counts"]["organization_edges"] == 8
    assert fixture["sole_full_e2e"] is True


def test_small_fixtures_are_isolated_and_match_source_definitions() -> None:
    fragment = _json(
        CONFIG_ROOT / "organizations.d/enterprise-scrum-organization.json"
    )
    fixture_catalog = _json(
        ROOT / "tests/fixtures/scenarios/organization-small-compositions.json"
    )
    source = {row["key"]: row for row in fragment["test_only_fixtures"]}

    assert fixture_catalog["production_seed_allowed"] is False
    assert len(fixture_catalog["fixtures"]) == 4
    assert sorted(row["expected_total_team_count"] for row in fixture_catalog["fixtures"]) == [
        2,
        2,
        3,
        3,
    ]
    for row in fixture_catalog["fixtures"]:
        source_row = source[row["key"]]
        assert source_row["production_seed_allowed"] is False
        assert row["team_blueprint_counts"] == source_row["composition_overrides"][
            "custom_composition"
        ]["team_blueprint_counts"]
        assert row["expected_diagnostic_codes"] == source_row[
            "expected_diagnostic_codes"
        ]
