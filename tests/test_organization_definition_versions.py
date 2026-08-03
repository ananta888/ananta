from __future__ import annotations

from copy import deepcopy

import pytest

from agent.db_models.organizations import (
    OrganizationBlueprintRevisionDB,
    OrganizationPolicyRevisionDB,
    RoleTemplateRevisionDB,
    TeamBlueprintRevisionDB,
    WorkflowDefinitionRevisionDB,
)
from agent.models.organization_models import canonical_definition_sha256
from agent.services.organization_reconciliation_service import OrganizationReconciliationService


def _definition() -> dict:
    return {
        "key": "enterprise_delivery",
        "version": 1,
        "role_slots": [
            {"slot_id": "developer", "role_template_ref": "developer@1"},
            {"slot_id": "product_owner", "role_template_ref": "product_owner@1"},
        ],
        "workflows": [{"step_id": "implement", "owner_role_ref": "developer@1", "depends_on": []}],
        "relations": [
            {
                "relation_id": "delivery_to_quality",
                "kind": "supplies",
                "source_unit_ref": "delivery",
                "target_unit_ref": "quality",
            }
        ],
        "policies": ["delivery_policy@1", "grounding_policy@1"],
        "referenced_versions": {
            "delivery_team": "delivery_team@1",
            "quality_team": "quality_team@1",
        },
    }


def test_order_insensitive_definition_collections_keep_the_same_revision() -> None:
    original = _definition()
    reordered = deepcopy(original)
    reordered["role_slots"].reverse()
    reordered["policies"].reverse()

    assert canonical_definition_sha256(reordered) == canonical_definition_sha256(original)


@pytest.mark.parametrize(
    ("section", "replacement"),
    (
        (
            "role_slots",
            [{"slot_id": "reviewer", "role_template_ref": "reviewer@2"}],
        ),
        (
            "workflows",
            [{"step_id": "verify", "owner_role_ref": "reviewer@2", "depends_on": ["implement"]}],
        ),
        (
            "relations",
            [
                {
                    "relation_id": "delivery_to_quality",
                    "kind": "gates",
                    "source_unit_ref": "quality",
                    "target_unit_ref": "delivery",
                }
            ],
        ),
        ("policies", ["delivery_policy@2"]),
        ("referenced_versions", {"delivery_team": "delivery_team@2"}),
    ),
)
def test_contract_changes_create_revision_drift(section: str, replacement: object) -> None:
    current = _definition()
    desired = deepcopy(current)
    desired[section] = replacement

    plan = OrganizationReconciliationService().plan(
        definition_key="enterprise_delivery",
        current_definition=current,
        desired_definition=desired,
    )

    assert plan.current_revision != plan.desired_revision
    assert [entry.path for entry in plan.drift] == [f"$.{section}"]
    assert plan.planned_writes == (f"create_definition_revision:{section}",)
    assert plan.applicable is True


def test_local_override_conflict_preserves_the_override_and_blocks_apply() -> None:
    current = _definition()
    desired = deepcopy(current)
    desired["role_slots"] = [{"slot_id": "developer", "role_template_ref": "developer@2"}]

    plan = OrganizationReconciliationService().plan(
        definition_key="enterprise_delivery",
        current_definition=current,
        desired_definition=desired,
        local_override_paths=("$.role_slots.developer",),
    )

    assert plan.applicable is False
    assert plan.preserved_local_overrides == ("$.role_slots.developer",)
    assert plan.blockers == ("local_override_conflict:$.role_slots",)


def test_explicit_seed_removal_archives_the_definition_and_preserves_active_snapshots() -> None:
    plan = OrganizationReconciliationService().plan(
        definition_key="enterprise_delivery",
        current_definition=_definition(),
        desired_definition=_definition(),
        active_instance_snapshot_revisions=("definition-revision-one",),
        removed_from_seed=True,
        removal_lifecycle_approved=True,
    )

    assert plan.applicable is True
    assert plan.planned_writes == (
        "archive_seed_definition_revision",
        "preserve_active_instance_snapshots",
    )


@pytest.mark.parametrize(
    ("model", "expected_columns"),
    (
        (RoleTemplateRevisionDB, ("tenant_id", "project_id", "definition_key", "version")),
        (TeamBlueprintRevisionDB, ("tenant_id", "project_id", "definition_key", "version")),
        (WorkflowDefinitionRevisionDB, ("tenant_id", "project_id", "definition_key", "version")),
        (OrganizationBlueprintRevisionDB, ("tenant_id", "project_id", "definition_key", "version")),
        (OrganizationPolicyRevisionDB, ("tenant_id", "project_id", "policy_key", "revision")),
    ),
)
def test_definition_revisions_are_unique_inside_tenant_project_scope(model, expected_columns: tuple[str, ...]) -> None:
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert expected_columns in unique_column_sets
