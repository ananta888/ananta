from __future__ import annotations

import pytest

from agent.models.organization_models import canonical_definition_sha256
from agent.models.team_models import (
    BlueprintArtifactDefinition,
    BlueprintBundleDefinition,
    BlueprintBundleMemberAssignment,
    BlueprintBundleRoleDefinition,
    BlueprintBundleTeamDefinition,
    BlueprintBundleTemplate,
    TeamBlueprintBundle,
)
from agent.services.organization_bundle_migration_service import (
    OrganizationBundleMigrationError,
    OrganizationBundleMigrationService,
)


def _legacy_bundle(*, include_member: bool = False) -> TeamBlueprintBundle:
    members = (
        [BlueprintBundleMemberAssignment(agent_url="https://worker.invalid/legacy", role_name="Developer")]
        if include_member
        else []
    )
    return TeamBlueprintBundle(
        schema_version="1.0",
        mode="full",
        parts=["blueprint", "templates", "team"],
        blueprint=BlueprintBundleDefinition(
            name="Legacy Delivery",
            description="A deterministic compatibility fixture.",
            base_team_type_name="Scrum",
            roles=[
                BlueprintBundleRoleDefinition(
                    name="Product Owner",
                    template_name="Product Owner",
                    sort_order=1,
                    config={"min_count": 1, "default_count": 1, "max_count": 1},
                ),
                BlueprintBundleRoleDefinition(
                    name="Developer",
                    template_name="Developer",
                    sort_order=2,
                    config={"min_count": 1, "default_count": 2, "max_count": 4},
                ),
            ],
            artifacts=[BlueprintArtifactDefinition(kind="implementation", title="Implementation", sort_order=1)],
        ),
        templates=[
            BlueprintBundleTemplate(
                name="Developer",
                description="Executes Hub-delegated implementation work.",
                prompt_template="Execute only the assigned implementation task.",
            ),
            BlueprintBundleTemplate(
                name="Product Owner",
                description="Clarifies the delegated product outcome.",
                prompt_template="Clarify only the assigned product decision.",
            ),
        ],
        team=BlueprintBundleTeamDefinition(
            name="Legacy Delivery One",
            blueprint_name="Legacy Delivery",
            members=members,
        ),
        bundle_metadata={"team_kind": "delivery"},
    )


def test_v1_team_slice_migration_is_deterministic_and_does_not_invent_an_organization() -> None:
    service = OrganizationBundleMigrationService()

    first = service.migrate_v1_team_slice(_legacy_bundle())
    second = service.migrate_v1_team_slice(_legacy_bundle())

    assert first.source_digest == second.source_digest
    assert first.target_digest == second.target_digest
    assert first.bundle.model_dump(mode="json") == second.bundle.model_dump(mode="json")
    assert first.bundle.organization_blueprints == []
    assert first.bundle.organization_instances == []
    assert first.bundle.handoff_definitions == []
    assert first.bundle.assignments == []
    assert "organization_topology_and_relations_not_inferred" in first.warnings


def test_migrated_revisions_are_hash_bound_and_workers_remain_non_orchestrating() -> None:
    result = OrganizationBundleMigrationService().migrate_v1_team_slice(_legacy_bundle())
    revisions = [
        *result.bundle.role_templates,
        *result.bundle.team_blueprints,
        *result.bundle.workflow_definitions,
        *result.bundle.policies,
    ]

    assert revisions
    assert all(item.content_hash == canonical_definition_sha256(item.definition) for item in revisions)
    assert all(
        "worker_orchestration" in item.definition["capability_policy"]["forbidden"]
        for item in result.bundle.role_templates
    )
    assert all(
        item.definition["contract_ref"].endswith("/legacy-team-compatibility") for item in result.bundle.policies
    )


def test_legacy_members_are_omitted_until_local_assignment_rebinding() -> None:
    result = OrganizationBundleMigrationService().migrate_v1_team_slice(_legacy_bundle(include_member=True))

    assert result.bundle.include_assignments is False
    assert result.bundle.assignments == []
    assert "legacy_member_assignments_omitted" in result.warnings


@pytest.mark.parametrize(
    ("payload", "reason_code"),
    (
        ({"schema_version": "2.0"}, "organization_bundle_v1_schema_required"),
        ({"schema_version": "1.0"}, "organization_bundle_v1_blueprint_required"),
    ),
)
def test_invalid_legacy_envelopes_fail_with_stable_reasons(payload: dict, reason_code: str) -> None:
    with pytest.raises(OrganizationBundleMigrationError, match=reason_code):
        OrganizationBundleMigrationService().migrate_v1_team_slice(payload)
