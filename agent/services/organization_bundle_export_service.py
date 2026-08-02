"""Cross-scope Organization Bundle v2 export with optional recompile recipes."""

from __future__ import annotations

import secrets
from collections import Counter
from typing import Any

from sqlmodel import Session, select

from agent.db_models.organizations import (
    OrganizationBlueprintRevisionDB,
    OrganizationHandoffDefinitionRevisionDB,
    OrganizationInstanceDB,
    OrganizationLimitProfileRevisionDB,
    OrganizationPolicyRevisionDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationUnitDB,
    RoleTemplateRevisionDB,
    TeamBlueprintRevisionDB,
    WorkflowDefinitionRevisionDB,
)
from agent.models.organization_models import (
    OrganizationLimitProfile,
    canonical_definition_sha256,
)
from agent.models.team_models import (
    OrganizationBlueprintBundleV2,
    PortableDefinitionRevision,
    PortableOrganizationInstance,
    RedactedOrganizationAssignment,
)
from agent.repositories.organizations.definitions import SqlOrganizationDefinitionRepository
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
)


class OrganizationBundleExportError(RuntimeError):
    def __init__(self, reason_code: str, *, public_status: int = 409) -> None:
        self.reason_code = reason_code
        self.public_status = public_status
        super().__init__(reason_code)


class OrganizationBundleExportService:
    def __init__(self, *, catalog=None) -> None:
        self._catalog = catalog

    def export(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        include_instances: bool = False,
        include_assignments: bool = False,
    ) -> OrganizationBlueprintBundleV2:
        organization = session.exec(
            select(OrganizationInstanceDB)
            .where(OrganizationInstanceDB.tenant_id == tenant_id)
            .where(OrganizationInstanceDB.project_id == project_id)
            .where(OrganizationInstanceDB.organization_id == organization_id)
        ).first()
        if organization is None:
            raise OrganizationBundleExportError("organization_not_found", public_status=404)
        if include_assignments and not include_instances:
            raise OrganizationBundleExportError(
                "organization_bundle_assignment_requires_instance_recipe",
                public_status=422,
            )

        definitions = SqlOrganizationDefinitionRepository(session)
        if self._catalog is not None:
            definitions = FileCatalogDefinitionRepositoryAdapter(
                definitions,
                self._catalog,
                session,
            )

        organization_definition = self._one_definition(
            definitions,
            OrganizationBlueprintRevisionDB,
            tenant_id,
            project_id,
            definition_key=organization.definition_key,
            version=organization.definition_version,
        )
        if organization_definition is None:
            raise OrganizationBundleExportError("organization_export_blueprint_missing")

        team_refs: set[tuple[str, int]] = set()
        organization_definition_json = dict(organization_definition.definition_json or {})
        for value in list(organization_definition_json.get("units") or []) + list(
            organization_definition_json.get("unit_groups") or []
        ):
            parsed = _split_ref(str(value.get("team_blueprint_ref") or ""))
            if parsed:
                team_refs.add(parsed)
        role_refs: set[tuple[str, int]] = set()
        handoff_refs: set[tuple[str, int]] = set()
        for value in organization_definition_json.get("relations") or []:
            parsed = _split_ref(str(value.get("handoff_contract_ref") or ""))
            if parsed:
                handoff_refs.add(parsed)

        team_rows = self._definitions(definitions, TeamBlueprintRevisionDB, tenant_id, project_id, team_refs)
        workflow_refs: set[tuple[str, int]] = set()
        policy_refs: set[tuple[str, int]] = set()
        for row in team_rows:
            definition = dict(row.definition_json or {})
            role_refs.update(
                parsed
                for parsed in (
                    _split_ref(str(slot.get("role_template_ref") or "")) for slot in definition.get("role_slots") or []
                )
                if parsed
            )
            workflow_key = getattr(row, "workflow_definition_key", None)
            workflow_version = getattr(row, "workflow_definition_version", None)
            if workflow_key and workflow_version:
                workflow_refs.add((workflow_key, workflow_version))
            workflow_ref = str(definition.get("workflow_ref") or "")
            parsed = _split_ref(workflow_ref)
            if parsed:
                workflow_refs.add(parsed)
            policy_refs.update(filter(None, (_split_ref(value) for value in definition.get("policies") or [])))
        role_rows = []
        pending_role_refs = set(role_refs)
        loaded_role_refs: set[tuple[str, int]] = set()
        while pending_role_refs:
            batch = self._definitions(
                definitions,
                RoleTemplateRevisionDB,
                tenant_id,
                project_id,
                pending_role_refs,
            )
            role_rows.extend(batch)
            loaded_role_refs.update(pending_role_refs)
            discovered: set[tuple[str, int]] = set()
            for row in batch:
                definition = dict(row.definition_json or {})
                discovered.update(
                    parsed for parsed in (_split_ref(str(value)) for value in definition.get("extends") or []) if parsed
                )
                for field in ("grounding_policy_ref", "task_proposal_policy_ref"):
                    parsed = _split_ref(str(definition.get(field) or ""))
                    if parsed:
                        policy_refs.add(parsed)
            pending_role_refs = discovered - loaded_role_refs
        for value in ((organization_definition_json.get("budgets") or {}).get("policy_ref"),):
            parsed = _split_ref(str(value or ""))
            if parsed:
                policy_refs.add(parsed)

        workflow_rows = self._definitions(
            definitions,
            WorkflowDefinitionRevisionDB,
            tenant_id,
            project_id,
            workflow_refs,
        )
        for row in workflow_rows:
            for step in _workflow_definition(row).get("steps") or []:
                parsed = _split_ref(str(step.get("handoff_ref") or ""))
                if parsed:
                    handoff_refs.add(parsed)
        handoff_rows = self._definitions(
            definitions,
            OrganizationHandoffDefinitionRevisionDB,
            tenant_id,
            project_id,
            handoff_refs,
        )
        # Bundle v2 transports revisioned limits separately from governance
        # policies, even though blueprints reference both with key@version.
        limit_ref = _require_ref(str(organization_definition_json.get("limit_policy_ref") or ""))
        policy_refs.discard(limit_ref)
        policy_rows = self._definitions(
            definitions,
            OrganizationPolicyRevisionDB,
            tenant_id,
            project_id,
            policy_refs,
        )

        limit_key, limit_revision = limit_ref
        limit_row = self._one_definition(
            definitions,
            OrganizationLimitProfileRevisionDB,
            tenant_id,
            project_id,
            policy_key=limit_key,
            revision=limit_revision,
        )
        if limit_row is None or limit_row.profile_hash != organization.effective_limit_profile_hash:
            raise OrganizationBundleExportError("organization_export_limit_profile_stale")

        portable_instances: list[PortableOrganizationInstance] = []
        portable_assignments: list[RedactedOrganizationAssignment] = []
        if include_instances:
            units = list(
                session.exec(
                    select(OrganizationUnitDB)
                    .where(OrganizationUnitDB.tenant_id == tenant_id)
                    .where(OrganizationUnitDB.project_id == project_id)
                    .where(OrganizationUnitDB.organization_id == organization_id)
                    .where(OrganizationUnitDB.lifecycle != "archived")
                ).all()
            )
            team_units = [row for row in units if row.unit_kind == "team"]
            custom_counts = dict(
                sorted(Counter(str(row.team_blueprint_key) for row in team_units if row.team_blueprint_key).items())
            )
            try:
                portable_instances.append(
                    PortableOrganizationInstance(
                        instance_key="root",
                        definition_ref=f"{organization.definition_key}@{organization.definition_version}",
                        name=organization.name,
                        composition_mode=organization.composition_mode,
                        team_count=(len(team_units) if organization.composition_mode == "standard" else None),
                        team_blueprint_counts=(custom_counts if organization.composition_mode == "custom" else None),
                        requested_lifecycle=("draft" if organization.lifecycle == "draft" else "validated"),
                    )
                )
            except ValueError as exc:
                raise OrganizationBundleExportError("organization_export_instance_recipe_invalid") from exc
            if include_assignments:
                portable_assignments = self._portable_assignments(
                    session,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                )

        bundle = OrganizationBlueprintBundleV2(
            bundle_metadata={
                "export_kind": (
                    "organization_recompile_bundle" if include_instances else "organization_definition_graph"
                ),
                "portability": "cross_tenant_project",
                "root_definition_ref": (f"{organization.definition_key}@{organization.definition_version}"),
                "instance_transport": ("target_recompile_recipe" if include_instances else "excluded"),
                "assignment_transport": ("pseudonymized_target_rebind" if include_assignments else "excluded"),
            },
            role_templates=[self._portable(row) for row in role_rows],
            team_blueprints=[self._portable(row) for row in team_rows],
            workflow_definitions=[self._portable_workflow(row) for row in workflow_rows],
            organization_blueprints=[self._portable(organization_definition)],
            handoff_definitions=[self._portable(row) for row in handoff_rows],
            policies=[self._portable(row) for row in policy_rows],
            limit_profiles=[self._portable_limit(limit_row)],
            organization_instances=portable_instances,
            include_assignments=include_assignments,
            assignments=portable_assignments,
        )
        if _contains_source_scope_value(
            bundle.model_dump(mode="json"),
            {tenant_id, project_id, organization_id},
        ):
            raise OrganizationBundleExportError("organization_export_definition_graph_contains_source_scope")
        return bundle

    @staticmethod
    def _portable_assignments(
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> list[RedactedOrganizationAssignment]:
        slots = {
            row.id: row
            for row in session.exec(
                select(OrganizationRoleSlotDB)
                .where(OrganizationRoleSlotDB.tenant_id == tenant_id)
                .where(OrganizationRoleSlotDB.project_id == project_id)
                .where(OrganizationRoleSlotDB.organization_id == organization_id)
            ).all()
        }
        units = {
            row.id: row
            for row in session.exec(
                select(OrganizationUnitDB)
                .where(OrganizationUnitDB.tenant_id == tenant_id)
                .where(OrganizationUnitDB.project_id == project_id)
                .where(OrganizationUnitDB.organization_id == organization_id)
            ).all()
        }
        rows = list(
            session.exec(
                select(OrganizationRoleAssignmentDB)
                .where(OrganizationRoleAssignmentDB.tenant_id == tenant_id)
                .where(OrganizationRoleAssignmentDB.project_id == project_id)
                .where(OrganizationRoleAssignmentDB.organization_id == organization_id)
                .where(OrganizationRoleAssignmentDB.lifecycle.in_(("proposed", "active")))
                .order_by(
                    OrganizationRoleAssignmentDB.role_slot_id,
                    OrganizationRoleAssignmentDB.id,
                )
            ).all()
        )
        principal_refs: dict[str, str] = {}
        exported: list[RedactedOrganizationAssignment] = []
        for row in rows:
            slot = slots.get(row.role_slot_id)
            unit = units.get(slot.unit_id) if slot else None
            if slot is None or unit is None:
                raise OrganizationBundleExportError("organization_export_assignment_lineage_missing")
            principal_refs.setdefault(row.agent_url, f"principal-{secrets.token_urlsafe(18)}")
            exported.append(
                RedactedOrganizationAssignment(
                    instance_key="root",
                    unit_key=unit.unit_key,
                    role_slot_key=slot.slot_key,
                    principal_ref=principal_refs[row.agent_url],
                )
            )
        return exported

    @staticmethod
    def _one_definition(repository, model, tenant_id, project_id, **identity):
        key = str(identity.get("definition_key") or identity.get("policy_key") or "")
        version = int(identity.get("version") or identity.get("revision") or 0)
        getter = {
            RoleTemplateRevisionDB: repository.get_role_template,
            TeamBlueprintRevisionDB: repository.get_team_blueprint,
            WorkflowDefinitionRevisionDB: repository.get_workflow,
            OrganizationBlueprintRevisionDB: repository.get_organization_blueprint,
            OrganizationHandoffDefinitionRevisionDB: repository.get_handoff,
            OrganizationPolicyRevisionDB: repository.get_policy,
            OrganizationLimitProfileRevisionDB: repository.get_limit_profile,
        }[model]
        return getter(tenant_id, project_id, key, version)

    def _definitions(self, repository, model, tenant_id, project_id, identities):
        rows = []
        key_field = "policy_key" if model is OrganizationPolicyRevisionDB else "definition_key"
        version_field = "revision" if model is OrganizationPolicyRevisionDB else "version"
        for key, version in sorted(identities):
            row = self._one_definition(
                repository,
                model,
                tenant_id,
                project_id,
                **{key_field: key, version_field: version},
            )
            if row is None:
                raise OrganizationBundleExportError("organization_export_referenced_definition_missing")
            rows.append(row)
        return rows

    @staticmethod
    def _portable(row) -> PortableDefinitionRevision:
        definition = dict(row.definition_json or {})
        if canonical_definition_sha256(definition) != row.content_hash:
            raise OrganizationBundleExportError("organization_export_definition_hash_mismatch")
        key = getattr(row, "definition_key", None) or getattr(row, "policy_key", None)
        version = getattr(row, "version", None) or getattr(row, "revision", None)
        return PortableDefinitionRevision(
            key=key,
            version=version,
            lifecycle=row.lifecycle,
            content_hash=row.content_hash,
            definition=definition,
        )

    @staticmethod
    def _portable_workflow(row) -> PortableDefinitionRevision:
        stored_definition = getattr(row, "definition_json", None)
        if stored_definition is not None:
            definition = dict(stored_definition)
            if canonical_definition_sha256(definition) != row.content_hash:
                raise OrganizationBundleExportError("organization_export_workflow_hash_mismatch")
            return PortableDefinitionRevision(
                key=row.definition_key,
                version=row.version,
                lifecycle=row.lifecycle,
                content_hash=row.content_hash,
                definition=definition,
            )
        base = {
            "key": row.definition_key,
            "version": row.version,
            "mode": row.mode,
            "default_failure_policy": row.default_failure_policy,
            "steps": list(row.steps_json or []),
        }
        candidates = [base]
        for include_checks in (False, True):
            for include_capabilities in (False, True):
                value = dict(base)
                if include_checks:
                    value["checks"] = dict(row.checks_json or {})
                if include_capabilities:
                    value["required_capabilities"] = list(row.required_capabilities or [])
                candidates.append(value)
        definition = next(
            (value for value in candidates if canonical_definition_sha256(value) == row.content_hash),
            None,
        )
        if definition is None:
            raise OrganizationBundleExportError("organization_export_workflow_hash_mismatch")
        return PortableDefinitionRevision(
            key=row.definition_key,
            version=row.version,
            lifecycle=row.lifecycle,
            content_hash=row.content_hash,
            definition=definition,
        )

    @staticmethod
    def _portable_limit(row) -> PortableDefinitionRevision:
        definition = OrganizationLimitProfile(
            policy_id=row.policy_key,
            revision=row.revision,
            **dict(row.limits_json or {}),
        ).model_dump(mode="json")
        if canonical_definition_sha256(definition) != row.profile_hash:
            raise OrganizationBundleExportError("organization_export_limit_profile_hash_mismatch")
        return PortableDefinitionRevision(
            key=row.policy_key,
            version=row.revision,
            lifecycle=row.lifecycle,
            content_hash=row.profile_hash,
            definition=definition,
        )


def _workflow_definition(row) -> dict[str, Any]:
    stored = getattr(row, "definition_json", None)
    if stored is not None:
        return dict(stored)
    definition: dict[str, Any] = {
        "key": row.definition_key,
        "version": row.version,
        "mode": row.mode,
        "default_failure_policy": row.default_failure_policy,
        "steps": list(row.steps_json or []),
    }
    if row.checks_json:
        definition["checks"] = dict(row.checks_json)
    if row.required_capabilities:
        definition["required_capabilities"] = list(row.required_capabilities)
    return definition


def _contains_source_scope_value(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return any(_contains_source_scope_value(child, forbidden) for child in value.values())
    if isinstance(value, list):
        return any(_contains_source_scope_value(child, forbidden) for child in value)
    return isinstance(value, str) and value in forbidden


def _split_ref(value: str) -> tuple[str, int] | None:
    key, separator, version = str(value or "").rpartition("@")
    return (key, int(version)) if separator and key and version.isdigit() and int(version) >= 1 else None


def _require_ref(value: str) -> tuple[str, int]:
    parsed = _split_ref(value)
    if parsed is None:
        raise OrganizationBundleExportError("organization_export_definition_ref_invalid")
    return parsed


__all__ = ["OrganizationBundleExportError", "OrganizationBundleExportService"]
