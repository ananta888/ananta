from __future__ import annotations

from agent.services.seed_blueprint_catalog import SeedBlueprintCatalog
from agent.services.seed_template_catalog import SeedTemplateCatalog


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
    assert all("Story/domain-first working contract" in template["prompt_template"] for template in templates)


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
    assert [step["id"] for step in steps] == ["story", "domain", "implementation", "verification"]

    by_id = {step["id"]: step for step in steps}
    assert by_id["story"]["produces"] == ["User Story", "Acceptance Criteria", "Story Constraints"]
    assert by_id["domain"]["depends_on"] == ["story"]
    assert by_id["domain"]["consumes"] == ["User Story", "Acceptance Criteria", "Story Constraints"]
    assert by_id["implementation"]["depends_on"] == ["domain"]
    assert "Domain Model" in by_id["implementation"]["consumes"]
    assert by_id["verification"]["depends_on"] == ["implementation"]
    assert by_id["verification"]["gate"] is True
    assert by_id["verification"]["checks"]["verification_required"] is True
