from __future__ import annotations

from agent.services.organization_reconciliation_service import (
    OrganizationReconciliationService,
)


def _definition(*, delivery_count: int = 2) -> dict:
    return {
        "units": [{"unit_key": "delivery", "count": delivery_count}],
        "unit_groups": [{"group_id": "product_delivery", "count": delivery_count}],
        "role_slots": [{"slot_id": "developer", "min_count": 1}],
        "workflows": ["enterprise_product_evolution@1"],
        "relations": [{"relation_id": "portfolio_governs_delivery"}],
        "policies": ["strict_delivery_separation@1"],
        "referenced_versions": ["enterprise_product_delivery_scrum@1"],
    }


def test_reconcile_is_idempotent_for_identical_definitions() -> None:
    definition = _definition()
    service = OrganizationReconciliationService()

    first = service.plan(
        definition_key="enterprise_scrum_organization",
        current_definition=definition,
        desired_definition=definition,
    )
    replay = service.plan(
        definition_key="enterprise_scrum_organization",
        current_definition=definition,
        desired_definition=definition,
    )

    assert first == replay
    assert first.drift == ()
    assert first.planned_writes == ()
    assert first.applicable is True


def test_nested_local_override_blocks_parent_section_replacement() -> None:
    service = OrganizationReconciliationService()

    plan = service.plan(
        definition_key="enterprise_scrum_organization",
        current_definition=_definition(delivery_count=2),
        desired_definition=_definition(delivery_count=3),
        local_override_paths=("$.units.delivery.capacity",),
    )

    entry = next(row for row in plan.drift if row.path == "$.units")
    assert entry.conflict is True
    assert "local_override_conflict:$.units" in plan.blockers
    assert plan.applicable is False


def test_active_snapshots_are_preserved_during_definition_upgrade() -> None:
    service = OrganizationReconciliationService()

    plan = service.plan(
        definition_key="enterprise_scrum_organization",
        current_definition=_definition(delivery_count=2),
        desired_definition=_definition(delivery_count=3),
        active_instance_snapshot_revisions=("snapshot-revision-a",),
    )

    assert plan.applicable is True
    assert "preserve_active_instance_snapshots" in plan.planned_writes
    assert "create_definition_revision:units" in plan.planned_writes


def test_removed_seed_requires_explicit_lifecycle_and_is_never_blindly_deleted() -> None:
    service = OrganizationReconciliationService()

    blocked = service.plan(
        definition_key="retired_definition",
        current_definition=_definition(),
        desired_definition={},
        removed_from_seed=True,
    )
    approved = service.plan(
        definition_key="retired_definition",
        current_definition=_definition(),
        desired_definition={},
        removed_from_seed=True,
        removal_lifecycle_approved=True,
    )

    assert "seed_removal_requires_explicit_lifecycle" in blocked.blockers
    assert "archive_seed_definition_revision" in approved.planned_writes
    assert all("delete" not in write for write in approved.planned_writes)
