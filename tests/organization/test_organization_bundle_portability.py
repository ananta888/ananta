from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.models.organization_models import canonical_definition_sha256
from agent.services.organization_bundle_export_service import (
    OrganizationBundleExportError,
    OrganizationBundleExportService,
)
from tests.organization_support import organization_limits


class _Result:
    def __init__(self, value) -> None:
        self._values = value if isinstance(value, list) else [value]

    def first(self):
        return self._values[0] if self._values else None

    def all(self):
        return list(self._values)


class _Session:
    def __init__(self, organization, *, runtime_rows=None) -> None:
        self.organization = organization
        self.runtime_rows = runtime_rows or {}

    def exec(self, statement):
        rendered = str(statement)
        if "organization_instances" in rendered:
            return _Result(self.organization)
        for table, rows in self.runtime_rows.items():
            if table in rendered:
                return _Result(rows)
        return _Result([])


class _Definitions:
    def __init__(self, organization_definition, policy, limit_profile) -> None:
        self.organization_definition = organization_definition
        self.policy = policy
        self.limit_profile = limit_profile

    def get_organization_blueprint(self, *_args):
        return self.organization_definition

    def get_policy(self, *_args):
        return self.policy

    def get_limit_profile(self, *_args):
        return self.limit_profile

    def get_role_template(self, *_args):
        return None

    def get_team_blueprint(self, *_args):
        return None

    def get_workflow(self, *_args):
        return None

    def get_handoff(self, *_args):
        return None


def _export_context(monkeypatch):
    source_tenant = "source-tenant-private"
    source_project = "source-project-private"
    source_organization = "source-organization-private"
    limits = organization_limits()
    organization_definition = {
        "key": "portable_organization",
        "version": 1,
        "units": [],
        "unit_groups": [],
        "relations": [],
        "budgets": {"policy_ref": "portable_budget@1"},
        "limit_policy_ref": f"{limits.policy_id}@{limits.revision}",
    }
    budget_definition = {"policy_type": "budget"}
    definition_row = SimpleNamespace(
        definition_key="portable_organization",
        version=1,
        lifecycle="active",
        definition_json=organization_definition,
        content_hash=canonical_definition_sha256(organization_definition),
    )
    budget_row = SimpleNamespace(
        policy_key="portable_budget",
        revision=1,
        lifecycle="active",
        definition_json=budget_definition,
        content_hash=canonical_definition_sha256(budget_definition),
    )
    limit_row = SimpleNamespace(
        policy_key=limits.policy_id,
        revision=limits.revision,
        lifecycle="active",
        profile_hash=limits.content_hash(),
        limits_json=limits.model_dump(mode="json", exclude={"policy_id", "revision"}),
    )
    organization = SimpleNamespace(
        tenant_id=source_tenant,
        project_id=source_project,
        organization_id=source_organization,
        definition_key="portable_organization",
        definition_version=1,
        effective_limit_profile_hash=limits.content_hash(),
        name="Portable organization",
        composition_mode="standard",
        lifecycle="validated",
    )
    definitions = _Definitions(definition_row, budget_row, limit_row)
    monkeypatch.setattr(
        "agent.services.organization_bundle_export_service.SqlOrganizationDefinitionRepository",
        lambda _session: definitions,
    )
    return (
        OrganizationBundleExportService(),
        _Session(organization),
        source_tenant,
        source_project,
        source_organization,
    )


def test_export_contains_only_cross_scope_definition_graph(monkeypatch) -> None:
    service, session, tenant_id, project_id, organization_id = _export_context(monkeypatch)

    bundle = service.export(
        session=session,
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
    )
    payload = bundle.model_dump(mode="json")

    assert payload["bundle_metadata"] == {
        "export_kind": "organization_definition_graph",
        "portability": "cross_tenant_project",
        "root_definition_ref": "portable_organization@1",
        "instance_transport": "excluded",
        "assignment_transport": "excluded",
    }
    assert payload["organization_instances"] == []
    assert payload["include_assignments"] is False
    assert payload["assignments"] == []
    rendered = str(payload)
    assert tenant_id not in rendered
    assert project_id not in rendered
    assert organization_id not in rendered
    assert "compiled_plan" not in rendered


def test_assignment_export_requires_portable_instance_recipe(monkeypatch) -> None:
    service, session, tenant_id, project_id, organization_id = _export_context(monkeypatch)

    with pytest.raises(OrganizationBundleExportError) as exc:
        service.export(
            session=session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            include_assignments=True,
        )

    assert exc.value.reason_code == "organization_bundle_assignment_requires_instance_recipe"


def test_explicit_runtime_export_uses_target_recompile_recipe_and_redacted_principal(
    monkeypatch,
) -> None:
    service, session, tenant_id, project_id, organization_id = _export_context(monkeypatch)
    unit = SimpleNamespace(
        id="source-unit-private",
        unit_key="delivery-one",
        unit_kind="team",
        team_blueprint_key="delivery_team",
    )
    second_unit = SimpleNamespace(
        id="source-unit-two-private",
        unit_key="delivery-two",
        unit_kind="team",
        team_blueprint_key="delivery_team",
    )
    slot = SimpleNamespace(
        id="source-slot-private",
        unit_id=unit.id,
        slot_key="developer",
    )
    assignment = SimpleNamespace(
        id="source-assignment-private",
        role_slot_id=slot.id,
        agent_url="https://source-agent.invalid",
    )
    session.runtime_rows = {
        "organization_units": [unit, second_unit],
        "organization_role_slots": [slot],
        "organization_role_assignments": [assignment],
    }

    bundle = service.export(
        session=session,
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        include_instances=True,
        include_assignments=True,
    )
    payload = bundle.model_dump(mode="json")

    assert payload["bundle_metadata"]["instance_transport"] == "target_recompile_recipe"
    assert payload["bundle_metadata"]["assignment_transport"] == "pseudonymized_target_rebind"
    assert payload["organization_instances"] == [
        {
            "instance_key": "root",
            "definition_ref": "portable_organization@1",
            "name": "Portable organization",
            "composition_mode": "standard",
            "team_count": 2,
            "team_blueprint_counts": None,
            "requested_lifecycle": "validated",
            "organization_id": None,
            "definition_revision": None,
            "effective_limit_profile_ref": None,
            "effective_limit_profile_revision": None,
            "effective_limit_profile_hash": None,
            "plan_digest": None,
            "topology_snapshot": None,
        }
    ]
    assert payload["assignments"][0]["principal_ref"].startswith("principal-")
    assert payload["assignments"][0]["principal_label"] is None
    assert payload["assignments"][0]["instance_key"] == "root"
    assert assignment.agent_url not in str(payload)
    assert assignment.id not in str(payload)
    assert slot.id not in str(payload)
    assert unit.id not in str(payload)
