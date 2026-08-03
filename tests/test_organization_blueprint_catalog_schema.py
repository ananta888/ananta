from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent.services.organization_definition_catalog_service import (
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
        "enterprise_scrum_medium_eight_team_reference"
    ]


def test_enterprise_definition_declares_data_driven_five_to_ten_band() -> None:
    catalog = OrganizationDefinitionCatalogService(repository_root=ROOT).reload()
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
