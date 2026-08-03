from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.services.organization_blueprint_validation_service import (
    OrganizationBlueprintValidationError,
)
from agent.services.organization_definition_catalog_service import (
    OrganizationDefinitionCatalogError,
    OrganizationDefinitionCatalogService,
)

ROOT = Path(__file__).resolve().parents[1]


def test_production_catalog_is_strict_and_omits_test_only_fixtures() -> None:
    catalog = OrganizationDefinitionCatalogService(repository_root=ROOT).reload()
    schema = json.loads(
        (ROOT / "schemas/blueprints/organization_blueprint_catalog.v1.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema).validate(catalog.aggregate)

    assert catalog.aggregate["test_only_fixtures"] == []
    assert [row["key"] for row in catalog.aggregate["acceptance_fixtures"]] == [
        "enterprise_scrum_medium_eight_team_reference",
        "lean_company_micro_five_roles",
        "lean_company_small_eight_roles",
        "lean_company_compact_twelve_roles",
        "lean_company_growing_sixteen_roles",
        "lean_company_scaled_twenty_roles",
    ]


def test_enterprise_definition_declares_data_driven_five_to_ten_band() -> None:
    catalog = OrganizationDefinitionCatalogService(repository_root=ROOT)
    catalog.reload()
    definition = catalog.get_organization_blueprint("enterprise_scrum_organization", 1)

    assert definition is not None
    standard = definition.standard_composition
    assert (standard.minimum, standard.default, standard.maximum) == (5, 8, 10)
    assert standard.scale_out_group == "product_delivery"
    assert len(standard.activation_order) == 3


def test_catalog_contains_all_required_enterprise_team_blueprints() -> None:
    catalog = OrganizationDefinitionCatalogService(repository_root=ROOT).reload()

    required = {
        "enterprise_product_delivery_scrum",
        "research_and_discovery",
        "proof_of_concept",
        "platform_devops_sre",
        "architecture_governance",
        "quality_security_release",
        "portfolio_product_coordination",
    }

    assert required.issubset({key for key, _version in catalog.team_blueprints})


def test_topology_projection_contract_is_a_valid_draft_2020_schema() -> None:
    schema = json.loads(
        (ROOT / "schemas/blueprints/organization_topology_projection.v1.json").read_text(encoding="utf-8")
    )

    Draft202012Validator.check_schema(schema)


def test_fragment_manifest_rejects_an_unknown_policy_reference(tmp_path: Path) -> None:
    repository = _catalog_copy(tmp_path)
    fragment_path = repository / "config/blueprints/standard/organizations.d/lean-company-organization.json"
    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    fragment["policy_refs"].append("missing_lean_policy@1")
    fragment_path.write_text(json.dumps(fragment), encoding="utf-8")

    with pytest.raises(OrganizationDefinitionCatalogError) as error:
        OrganizationDefinitionCatalogService(repository_root=repository).reload()

    assert error.value.reason_code == "organization_catalog_manifest_policy_not_found"


def test_handoff_rejects_a_non_gate_acceptance_policy(tmp_path: Path) -> None:
    repository = _catalog_copy(tmp_path)
    fragment_path = repository / "config/blueprints/standard/organizations.d/lean-company-organization.json"
    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    fragment["handoff_definitions"][0]["acceptance_gate_ref"] = "assignment_grounding@1"
    fragment_path.write_text(json.dumps(fragment), encoding="utf-8")

    with pytest.raises(OrganizationDefinitionCatalogError) as error:
        OrganizationDefinitionCatalogService(repository_root=repository).reload()

    assert error.value.reason_code == "organization_handoff_acceptance_gate_type_invalid"


def test_workflow_handoff_must_produce_every_required_artifact(tmp_path: Path) -> None:
    repository = _catalog_copy(tmp_path)
    workflow_path = repository / "config/blueprints/standard/workflows.d/lean-company-workflows.json"
    workflows = json.loads(workflow_path.read_text(encoding="utf-8"))
    direction_step = workflows["workflow_definitions"][0]["steps"][0]
    direction_step["outputs"].remove("accepted_requirements")
    workflow_path.write_text(json.dumps(workflows), encoding="utf-8")

    with pytest.raises(OrganizationBlueprintValidationError) as error:
        OrganizationDefinitionCatalogService(repository_root=repository).reload()

    assert str(error.value) == "WORKFLOW_HANDOFF_ARTIFACT_NOT_PRODUCED"


def _catalog_copy(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    shutil.copytree(ROOT / "schemas", repository / "schemas")
    shutil.copytree(ROOT / "config/blueprints/standard", repository / "config/blueprints/standard")
    return repository
