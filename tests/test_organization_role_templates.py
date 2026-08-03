from __future__ import annotations

from pathlib import Path

from agent.services.organization_definition_catalog_service import (
    OrganizationDefinitionCatalogService,
)

ROOT = Path(__file__).resolve().parents[1]
SCRUM_ACCOUNTABILITIES = {"product_owner", "scrum_master", "developer", "none"}


def test_versioned_role_templates_have_execution_and_governance_contracts() -> None:
    catalog = OrganizationDefinitionCatalogService(repository_root=ROOT).reload()

    assert len(catalog.role_templates) >= 60
    for (key, version), role in catalog.role_templates.items():
        assert role["key"] == key
        assert role["version"] == version
        assert role["scrum_accountability"] in SCRUM_ACCOUNTABILITIES
        assert role["specialization"]
        for field in (
            "mission",
            "scope",
            "responsibilities",
            "inputs",
            "outputs",
            "decision_rights",
            "handoffs",
            "capability_policy",
            "context_policy",
            "verification",
            "escalation",
            "prompt_template",
        ):
            assert role[field], f"{key}@{version} misses {field}"
        assert role["context_policy"]["source_allowlist_required"] is True


def test_official_scrum_accountabilities_remain_exactly_three() -> None:
    catalog = OrganizationDefinitionCatalogService(repository_root=ROOT).reload()
    accountabilities = {
        role["scrum_accountability"]
        for role in catalog.role_templates.values()
        if role["scrum_accountability"] != "none"
    }

    assert accountabilities == {"product_owner", "scrum_master", "developer"}


def test_specializations_do_not_inherit_governance_approval_by_prompt() -> None:
    catalog = OrganizationDefinitionCatalogService(repository_root=ROOT).reload()

    for role in catalog.role_templates.values():
        decision_rights = set(role["decision_rights"])
        assert "orchestrate_workers" not in decision_rights
        assert "write_hub_tasks" not in decision_rights
        assert role["escalation"]["target"] in {
            "hub",
            "portfolio",
            "architecture",
            "security",
            "quality",
            "human_operator",
        }
