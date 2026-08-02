from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

from agent.models.organization_models import canonical_definition_sha256
from agent.repositories.organizations.definition_impacts import (
    SqlOrganizationDefinitionImpactRepository,
)
from agent.routes.organization_blueprints import (
    _decode_cursor,
    _encode_cursor,
    organization_blueprints_bp,
)
from agent.routes.organization_route_support import OrganizationRouteError
from agent.services import blueprint_seed_service
from agent.services.organization_compile_application_service import (
    OrganizationCompileApplicationService,
)
from agent.services.organization_definition_application_service import (
    OrganizationDefinitionApplicationService,
)
from agent.services.organization_reconciliation_service import (
    OrganizationReconciliationService,
)
from tests.organization_support import FakeDefinitionCatalog, organization_definition


def test_definition_management_routes_are_additive_and_keep_preview_apply_split() -> None:
    app = Flask(__name__)
    app.register_blueprint(organization_blueprints_bp)
    rules = {(rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"}))) for rule in app.url_map.iter_rules()}

    assert ("/api/organization-blueprints/validate", ("POST",)) in rules
    assert ("/api/organization-blueprints", ("POST",)) in rules
    assert (
        "/api/organization-blueprints/<path:blueprint_key>/revisions",
        ("POST",),
    ) in rules
    assert (
        "/api/organization-blueprints/<path:blueprint_key>",
        ("PATCH",),
    ) in rules
    assert (
        "/api/organization-blueprints/<path:blueprint_key>/archive-preview",
        ("POST",),
    ) in rules
    assert (
        "/api/organization-blueprints/<path:blueprint_key>/archive",
        ("POST",),
    ) in rules
    assert (
        "/api/organization-blueprints/<path:blueprint_key>/reconcile-preview",
        ("POST",),
    ) in rules
    assert (
        "/api/organization-blueprints/<path:blueprint_key>/reconcile-apply",
        ("POST",),
    ) in rules


def test_reconcile_reports_entity_and_assignment_impact_without_snapshot_writes() -> None:
    current = {
        "version": 1,
        "units": [
            {"unit_key": "delivery", "parent_unit_ref": "portfolio"},
            {"unit_key": "quality", "parent_unit_ref": "portfolio"},
        ],
        "unit_groups": [{"group_id": "delivery_group", "default_count": 2}],
        "role_slots": [{"slot_id": "delivery_team@1:developer", "default_count": 1}],
        "relations": [{"relation_id": "quality_gates_delivery", "kind": "reviews"}],
        "policies": ["delivery_policy@1"],
    }
    desired = {
        **current,
        "version": 2,
        "units": [
            {"unit_key": "delivery", "parent_unit_ref": "portfolio_v2"},
            {"unit_key": "quality", "parent_unit_ref": "portfolio"},
        ],
        "policies": ["delivery_policy@2"],
        "role_slots": [{"slot_id": "delivery_team@1:developer", "default_count": 2}],
    }

    plan = OrganizationReconciliationService().plan(
        definition_key="enterprise_delivery",
        current_definition=current,
        desired_definition=desired,
        active_instance_snapshot_revisions=("snapshot-b", "snapshot-a"),
        active_assignment_links=(
            {
                "organization_id": "organization-a",
                "assignment_id": "assignment-a",
                "unit_key": "delivery",
                "group_key": "delivery_group",
                "role_slot_key": "developer",
                "role_definition_key": "delivery_team@1:developer",
                "lifecycle": "active",
            },
        ),
    )

    assert plan.current_revision == canonical_definition_sha256(current)
    assert plan.desired_revision == canonical_definition_sha256(desired)
    assert any(entry.section == "units" and entry.entity_key == "delivery" for entry in plan.entity_drift)
    assert plan.assignment_impacts[0].assignment_id == "assignment-a"
    assert plan.assignment_impacts[0].reasons == (
        "policy_workflow_or_reference_changed",
        "role_slot_definition_changed",
        "unit_definition_changed",
    )
    assert plan.preserved_snapshot_revisions == ("snapshot-a", "snapshot-b")
    assert "preserve_active_instance_snapshots" in plan.planned_writes
    assert all("snapshot" not in entry.path for entry in plan.drift)


def test_reconcile_blocks_jsonpath_array_override_conflicts() -> None:
    current = {"units": [{"unit_key": "delivery", "name": "Local"}]}
    desired = {"units": [{"unit_key": "delivery", "name": "Seed"}]}

    plan = OrganizationReconciliationService().plan(
        definition_key="enterprise_delivery",
        current_definition=current,
        desired_definition=desired,
        local_override_paths=("$.units[0].name",),
    )

    assert plan.drift[0].conflict is True
    assert plan.blockers == ("local_override_conflict:$.units",)


def test_relation_drift_is_visible_on_running_assignment_links() -> None:
    plan = OrganizationReconciliationService().plan(
        definition_key="enterprise_delivery",
        current_definition={"relations": [{"relation_id": "quality_gate", "kind": "reviews"}]},
        desired_definition={"relations": [{"relation_id": "quality_gate", "kind": "releases_for"}]},
        active_assignment_links=(
            {
                "organization_id": "organization-a",
                "assignment_id": "assignment-a",
                "unit_key": "delivery",
                "role_slot_key": "developer",
                "lifecycle": "active",
            },
        ),
    )

    assert plan.assignment_impacts[0].reasons == ("relation_definition_changed",)


def test_organization_seed_manifest_is_sorted_and_file_backed(monkeypatch) -> None:
    first = organization_definition()
    second = first.model_copy(update={"key": "architecture_organization"})
    snapshot = SimpleNamespace(
        organization_blueprints={
            (first.key, first.version): first,
            (second.key, second.version): second,
        }
    )

    class FakeCatalog:
        def snapshot(self):
            return snapshot

    monkeypatch.setattr(
        blueprint_seed_service,
        "get_organization_definition_catalog",
        lambda: FakeCatalog(),
    )

    manifest = blueprint_seed_service.load_organization_seed_fallback()

    assert [item["definition_ref"] for item in manifest] == [
        "architecture_organization@1",
        "enterprise_scrum_organization@1",
    ]
    assert {item["storage"] for item in manifest} == {"file_fallback"}
    assert {item["action"] for item in manifest} == {"available"}


def test_scoped_blueprint_list_uses_only_latest_active_revision_per_key() -> None:
    fallback = organization_definition()
    overlay = fallback.model_copy(update={"version": 2})
    overlay_row = SimpleNamespace(
        definition_key=fallback.key,
        version=2,
        lifecycle="active",
        content_hash=canonical_definition_sha256(overlay),
    )
    fallback_row = SimpleNamespace(
        lifecycle="active",
        content_hash=canonical_definition_sha256(fallback),
    )

    class FakeRepository:
        @staticmethod
        def list_organization_blueprint_revisions(*_args, **_kwargs):
            return [overlay_row]

        @staticmethod
        def get_organization_blueprint(_tenant, _project, _key, version):
            return overlay_row if version == 2 else fallback_row

    class FakeDefinitions:
        @staticmethod
        def get_organization_blueprint(_key, version):
            return overlay if version == 2 else fallback

    service = object.__new__(OrganizationCompileApplicationService)
    service._catalog = SimpleNamespace(  # noqa: SLF001 - isolated selector contract
        list_organization_blueprints=lambda: [fallback]
    )

    values = service._active_definitions(  # noqa: SLF001 - isolated selector contract
        definitions=FakeDefinitions(),
        repository=FakeRepository(),
        tenant_id="tenant-a",
        project_id="project-a",
    )

    assert values == [overlay]


def test_definition_mutation_hashes_transitive_execution_references() -> None:
    definitions = FakeDefinitionCatalog()
    definitions.content_hash_for_ref = lambda value: canonical_definition_sha256(  # type: ignore[attr-defined]
        {"definition_ref": value}
    )

    references = OrganizationDefinitionApplicationService._reference_hashes(  # noqa: SLF001
        organization_definition(),
        definitions=definitions,
    )

    assert "organization_limits@1" in references
    assert "enterprise_product_delivery_scrum@1" in references
    assert "enterprise_product_delivery_scrum_workflow@1" in references
    assert "enterprise_product_delivery_scrum_lead@1" in references
    assert "execution_policy@1" in references


def test_assignment_impact_query_is_project_scoped_and_does_not_select_agent_url() -> None:
    class EmptyResult:
        @staticmethod
        def all():
            return []

    class RecordingSession:
        statement = None

        def exec(self, statement):
            self.statement = statement
            return EmptyResult()

    session = RecordingSession()
    repository = SqlOrganizationDefinitionImpactRepository(session)

    assert (
        repository.list_assignment_links(
            "tenant-a",
            "project-a",
            "enterprise_delivery",
            2,
        )
        == []
    )
    compiled = session.statement.compile()
    selected_columns = str(compiled).split(" FROM ", 1)[0]

    assert "organization_role_assignments.agent_url" not in selected_columns
    assert "tenant-a" in compiled.params.values()
    assert "project-a" in compiled.params.values()


def test_definition_cursor_is_signed_and_bound_to_catalog_scope() -> None:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "definition-cursor-test-secret"
    scope = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "principal_id": "principal-a",
        "catalog_revision": "a" * 64,
    }

    with app.app_context():
        cursor = _encode_cursor(25, scope)
        assert _decode_cursor(cursor, scope) == 25
        with pytest.raises(OrganizationRouteError, match="organization_cursor_invalid"):
            _decode_cursor(cursor, {**scope, "project_id": "project-b"})
