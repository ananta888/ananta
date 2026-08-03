"""Atomic materialization of a previously compiled organization plan."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from agent.db_models.organizations import (
    OrganizationAdminGrantDB,
    OrganizationAuditOutboxDB,
    OrganizationInstanceDB,
    OrganizationMembershipDB,
    OrganizationOperationDB,
    OrganizationRelationDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.db_models.teams import TeamDB
from agent.models.organization_models import (
    CompiledOrganizationPlan,
    OrganizationInstantiationResult,
    VersionedDefinitionRef,
    canonical_sha256,
)
from agent.ports.organization_definitions import OrganizationLimitProfilePort
from agent.services.organization_unit_of_work import OrganizationUnitOfWork

_INSTANTIATION_RECEIPT_SCHEMA = "organization_instantiation_receipt.v1"


class OrganizationInstantiationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class OrganizationBlueprintInstantiationService:
    def __init__(
        self,
        *,
        limit_profiles: OrganizationLimitProfilePort,
        uow_factory: Callable[[], OrganizationUnitOfWork] = OrganizationUnitOfWork,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._limit_profiles = limit_profiles
        self._uow_factory = uow_factory
        self._fault_injector = fault_injector or (lambda _step: None)

    def instantiate(
        self,
        *,
        plan: CompiledOrganizationPlan,
        name: str,
        idempotency_key: str,
        expected_definition_revision: str,
        expected_plan_digest: str,
        principal_id: str,
        authorization_ref: str | None = None,
    ) -> OrganizationInstantiationResult:
        if canonical_sha256(plan.digest_payload()) != plan.plan_digest or plan.plan_digest != expected_plan_digest:
            raise OrganizationInstantiationError("organization_plan_digest_stale")
        if plan.definition_revision != expected_definition_revision:
            raise OrganizationInstantiationError("organization_definition_revision_stale")
        if plan.blockers:
            raise OrganizationInstantiationError("organization_plan_blocked")
        if not idempotency_key or not name.strip():
            raise OrganizationInstantiationError("organization_instantiation_binding_invalid")

        normalized_name = name.strip()
        request_digest = self._request_digest(
            plan_digest=plan.plan_digest,
            name=normalized_name,
            idempotency_key=idempotency_key,
            principal_id=principal_id,
        )
        result: OrganizationInstantiationResult | None = None
        with self._uow_factory() as uow:
            result = self._recover_applied_in_uow(
                uow=uow,
                tenant_id=plan.tenant_id,
                project_id=plan.project_id,
                plan_digest=plan.plan_digest,
                name=normalized_name,
                idempotency_key=idempotency_key,
                principal_id=principal_id,
                request_digest=request_digest,
                expected_organization_id=plan.organization_id,
                expected_definition_revision=plan.definition_revision,
                authorization_ref=authorization_ref,
            )
            if result is None:
                result = self._stage_new(
                    uow=uow,
                    plan=plan,
                    name=normalized_name,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    principal_id=principal_id,
                    authorization_ref=authorization_ref,
                )
        if result is None:
            raise OrganizationInstantiationError("organization_instantiation_result_missing")
        return result

    def recover_applied_instantiation(
        self,
        *,
        tenant_id: str,
        project_id: str,
        plan_digest: str,
        name: str,
        idempotency_key: str,
        principal_id: str,
        expected_organization_id: str,
        expected_definition_revision: str,
        authorization_ref: str | None = None,
    ) -> OrganizationInstantiationResult | None:
        """Recover one completed instantiate operation without recompiling its plan.

        The caller supplies only authenticated scope plus the immutable request
        binding persisted with the operation.  A missing operation is not an
        error and lets the caller continue through the normal compile path;
        every present but non-matching or incomplete operation fails closed.
        """

        normalized_name = name.strip()
        request_digest = self._request_digest(
            plan_digest=plan_digest,
            name=normalized_name,
            idempotency_key=idempotency_key,
            principal_id=principal_id,
        )
        with self._uow_factory() as uow:
            return self._recover_applied_in_uow(
                uow=uow,
                tenant_id=tenant_id,
                project_id=project_id,
                plan_digest=plan_digest,
                name=normalized_name,
                idempotency_key=idempotency_key,
                principal_id=principal_id,
                request_digest=request_digest,
                expected_organization_id=expected_organization_id,
                expected_definition_revision=expected_definition_revision,
                authorization_ref=authorization_ref,
            )

    def stage_in_uow(
        self,
        *,
        uow: OrganizationUnitOfWork,
        plan: CompiledOrganizationPlan,
        name: str,
        idempotency_key: str,
        principal_id: str,
    ) -> OrganizationInstantiationResult:
        """Stage one already-authorized plan in a caller-owned transaction.

        Bundle import uses this narrow seam so definitions, target-recompiled
        instances, teams, slots, assignments, snapshots, and audit outbox
        share one commit/rollback boundary. Authorization and one-shot grant
        consumption remain the application service's responsibility.
        """

        if canonical_sha256(plan.digest_payload()) != plan.plan_digest:
            raise OrganizationInstantiationError("organization_plan_digest_stale")
        if plan.blockers or not name.strip() or not idempotency_key:
            raise OrganizationInstantiationError("organization_instantiation_binding_invalid")
        if (
            uow.instances.get_scoped(
                plan.tenant_id,
                plan.project_id,
                plan.organization_id,
                for_update=True,
            )
            is not None
        ):
            raise OrganizationInstantiationError("organization_instance_conflict")
        request_digest = self._request_digest(
            plan_digest=plan.plan_digest,
            name=name.strip(),
            idempotency_key=idempotency_key,
            principal_id=principal_id,
        )
        return self._stage_new(
            uow=uow,
            plan=plan,
            name=name.strip(),
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            principal_id=principal_id,
            authorization_ref=None,
        )

    @staticmethod
    def _request_digest(
        *,
        plan_digest: str,
        name: str,
        idempotency_key: str,
        principal_id: str,
    ) -> str:
        return canonical_sha256(
            {
                "plan_digest": plan_digest,
                "name": name,
                "idempotency_key": idempotency_key,
                "principal_id": principal_id,
            }
        )

    @staticmethod
    def _recover_applied_in_uow(
        *,
        uow: OrganizationUnitOfWork,
        tenant_id: str,
        project_id: str,
        plan_digest: str,
        name: str,
        idempotency_key: str,
        principal_id: str,
        request_digest: str,
        expected_organization_id: str,
        expected_definition_revision: str,
        authorization_ref: str | None,
    ) -> OrganizationInstantiationResult | None:
        existing_operation = uow.operations.get_by_idempotency_key(
            tenant_id,
            project_id,
            "instantiate",
            idempotency_key,
            for_update=True,
        )
        if existing_operation is None:
            return None
        if (
            existing_operation.request_digest != request_digest
            or existing_operation.plan_digest != plan_digest
            or existing_operation.organization_id != expected_organization_id
            or existing_operation.expected_revision != expected_definition_revision
        ):
            raise OrganizationInstantiationError("organization_idempotency_key_conflict")
        if existing_operation.status != "applied" or not existing_operation.result_ref:
            raise OrganizationInstantiationError("organization_instantiation_in_progress")

        existing = uow.instances.get_scoped(
            tenant_id,
            project_id,
            existing_operation.result_ref,
        )
        if existing is None:
            raise OrganizationInstantiationError("organization_idempotency_result_missing")
        if (
            existing.organization_id != expected_organization_id
            or existing.idempotency_key != idempotency_key
            or existing.plan_digest != plan_digest
            or existing.definition_revision != expected_definition_revision
            or existing.name != name
            or existing.created_by != principal_id
        ):
            raise OrganizationInstantiationError("organization_idempotency_key_conflict")

        receipt = dict(existing_operation.result_json or {})
        receipt_result: OrganizationInstantiationResult | None = None
        if receipt:
            if receipt.get("schema") != _INSTANTIATION_RECEIPT_SCHEMA:
                raise OrganizationInstantiationError("organization_idempotency_result_missing")
            stored_authorization_ref = str(receipt.get("precreation_admin_grant_id") or "").strip()
            if stored_authorization_ref and stored_authorization_ref != str(authorization_ref or "").strip():
                raise OrganizationInstantiationError("organization_idempotency_admin_grant_conflict")
            try:
                receipt_result = OrganizationInstantiationResult.model_validate(receipt.get("result"))
            except (TypeError, ValueError) as exc:
                raise OrganizationInstantiationError("organization_idempotency_result_missing") from exc
            if (
                receipt_result.organization_id != existing.organization_id
                or receipt_result.definition_revision != existing.definition_revision
                or receipt_result.plan_digest != existing.plan_digest
                or receipt_result.idempotent_replay
            ):
                raise OrganizationInstantiationError("organization_idempotency_result_missing")

        snapshot = uow.snapshots.latest(tenant_id, project_id, existing.organization_id)
        units = uow.units.list_for_organization(tenant_id, project_id, existing.organization_id)
        admin_grant = next(
            (
                row
                for row in uow.admin_grants.list_for_organization(
                    tenant_id,
                    project_id,
                    existing.organization_id,
                )
                if row.principal_id == principal_id
                and row.grant_kind == "organization_admin"
                and row.policy_hash == existing.plan_digest
                and row.revoked_at is None
            ),
            None,
        )
        if admin_grant is None:
            raise OrganizationInstantiationError("organization_idempotency_admin_grant_missing")
        if receipt_result is not None:
            if receipt_result.organization_admin_grant_id != admin_grant.grant_id:
                raise OrganizationInstantiationError("organization_idempotency_result_missing")
            return receipt_result.model_copy(update={"idempotent_replay": True})
        return OrganizationInstantiationResult(
            organization_id=existing.organization_id,
            definition_revision=existing.definition_revision,
            plan_digest=existing.plan_digest,
            topology_snapshot_hash=snapshot.snapshot_hash if snapshot else existing.plan_digest,
            team_ids=[
                row.team_id
                for row in uow.team_links.list_for_organization(
                    tenant_id,
                    project_id,
                    existing.organization_id,
                )
            ],
            unit_ids=[row.id for row in units],
            role_slot_ids=[
                row.id
                for row in uow.role_slots.list_for_organization(
                    tenant_id,
                    project_id,
                    existing.organization_id,
                )
            ],
            relation_ids=[
                row.id
                for row in uow.relations.list_for_organization(
                    tenant_id,
                    project_id,
                    existing.organization_id,
                )
            ],
            organization_admin_grant_id=admin_grant.grant_id,
            idempotent_replay=True,
        )

    def _stage_new(
        self,
        *,
        uow,
        plan,
        name,
        idempotency_key,
        request_digest,
        principal_id,
        authorization_ref,
    ):
        current_limits = self._limit_profiles.resolve_limit_profile(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            policy_ref=plan.effective_limit_profile_ref,
        )
        if (
            current_limits.revision != plan.effective_limit_profile_revision
            or current_limits.content_hash() != plan.effective_limit_profile_hash
        ):
            raise OrganizationInstantiationError("organization_limit_profile_stale")
        self._enforce_concrete_limits(plan, current_limits)

        definition_ref = VersionedDefinitionRef.parse(plan.definition_ref)
        definition = uow.definitions.get_organization_blueprint(
            plan.tenant_id,
            plan.project_id,
            definition_ref.key,
            definition_ref.version,
        )
        if definition is None or definition.content_hash != plan.definition_revision:
            raise OrganizationInstantiationError("organization_definition_revision_stale")

        operation = OrganizationOperationDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            organization_id=plan.organization_id,
            operation_kind="instantiate",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            plan_digest=plan.plan_digest,
            expected_revision=plan.definition_revision,
            status="pending",
        )
        uow.operations.add(operation)
        self._fault_injector("operation")

        organization = OrganizationInstanceDB(
            organization_id=plan.organization_id,
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            name=name,
            definition_key=definition_ref.key,
            definition_version=definition_ref.version,
            definition_revision=plan.definition_revision,
            lifecycle="draft",
            effective_limit_profile_ref=plan.effective_limit_profile_ref,
            effective_limit_profile_revision=plan.effective_limit_profile_revision,
            effective_limit_profile_hash=plan.effective_limit_profile_hash,
            composition_mode=plan.composition_mode,
            plan_digest=plan.plan_digest,
            idempotency_key=idempotency_key,
            created_by=principal_id,
        )
        uow.instances.add(organization)
        self._fault_injector("organization")

        # Access ownership is part of the aggregate creation transaction.  The
        # creator can therefore never observe an organization that has no
        # administrator, even if a later materialization step fails.
        uow.memberships.add(
            OrganizationMembershipDB(
                tenant_id=plan.tenant_id,
                project_id=plan.project_id,
                organization_id=plan.organization_id,
                principal_id=principal_id,
                membership_kind="organization_admin",
            )
        )
        instance_admin_grant = OrganizationAdminGrantDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            organization_id=plan.organization_id,
            plan_digest=plan.plan_digest,
            principal_id=principal_id,
            grant_kind="organization_admin",
            policy_hash=plan.plan_digest,
            granted_by=principal_id,
        )
        uow.admin_grants.add(instance_admin_grant)
        self._fault_injector("organization_access")

        unit_by_key: dict[str, OrganizationUnitDB] = {}
        for compiled in _topological_units(plan):
            blueprint_ref = (
                VersionedDefinitionRef.parse(compiled.team_blueprint_ref) if compiled.team_blueprint_ref else None
            )
            row = OrganizationUnitDB(
                id=compiled.planned_id,
                tenant_id=plan.tenant_id,
                project_id=plan.project_id,
                organization_id=plan.organization_id,
                unit_key=compiled.unit_key,
                name=compiled.unit_key,
                unit_kind=compiled.unit_kind,
                parent_unit_id=unit_by_key[compiled.parent_unit_key].id if compiled.parent_unit_key else None,
                team_blueprint_key=blueprint_ref.key if blueprint_ref else None,
                team_blueprint_version=blueprint_ref.version if blueprint_ref else None,
                group_key=compiled.group_id,
                group_ordinal=compiled.group_ordinal,
                lifecycle="planned",
            )
            uow.units.add(row)
            unit_by_key[compiled.unit_key] = row
        uow.flush()
        self._fault_injector("units")

        team_ids: list[str] = []
        for compiled in plan.units:
            if not compiled.team_blueprint_ref:
                continue
            blueprint_ref = VersionedDefinitionRef.parse(compiled.team_blueprint_ref)
            blueprint = uow.definitions.get_team_blueprint(
                plan.tenant_id,
                plan.project_id,
                blueprint_ref.key,
                blueprint_ref.version,
            )
            if blueprint is None:
                raise OrganizationInstantiationError("team_blueprint_revision_missing")
            team_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ananta:organization-team:{compiled.planned_id}"))
            team_ids.append(team_id)
            uow.teams.add(
                TeamDB(
                    id=team_id,
                    name=f"{name} / {compiled.unit_key}",
                    description=f"Organization-managed team {compiled.unit_key}",
                    blueprint_id=blueprint.legacy_blueprint_id,
                    is_active=False,
                    blueprint_snapshot={
                        "definition_ref": compiled.team_blueprint_ref,
                        "definition_hash": blueprint.content_hash,
                        "organization_plan_digest": plan.plan_digest,
                    },
                )
            )
            uow.team_links.add(
                OrganizationTeamLinkDB(
                    tenant_id=plan.tenant_id,
                    project_id=plan.project_id,
                    organization_id=plan.organization_id,
                    unit_id=compiled.planned_id,
                    team_id=team_id,
                    lifecycle="planned",
                )
            )
        self._fault_injector("teams")

        for compiled in plan.role_slots:
            role_ref = VersionedDefinitionRef.parse(compiled.role_template_ref)
            uow.role_slots.add(
                OrganizationRoleSlotDB(
                    id=compiled.planned_id,
                    tenant_id=plan.tenant_id,
                    project_id=plan.project_id,
                    organization_id=plan.organization_id,
                    unit_id=unit_by_key[compiled.unit_key].id,
                    slot_key=compiled.slot_key,
                    role_template_key=role_ref.key,
                    role_template_version=role_ref.version,
                    required=compiled.required,
                    min_count=compiled.min_count,
                    default_count=compiled.default_count,
                    max_count=compiled.max_count,
                    assignment_policy=compiled.assignment_policy,
                    separation_of_duties=compiled.separation_of_duties,
                    overlays=compiled.overlays,
                )
            )
        self._fault_injector("role_slots")

        for compiled in plan.relations:
            handoff_ref = (
                VersionedDefinitionRef.parse(compiled.handoff_contract_ref) if compiled.handoff_contract_ref else None
            )
            uow.relations.add(
                OrganizationRelationDB(
                    id=compiled.planned_id,
                    tenant_id=plan.tenant_id,
                    project_id=plan.project_id,
                    organization_id=plan.organization_id,
                    relation_key=compiled.relation_key,
                    namespace=compiled.namespace,
                    kind=compiled.kind,
                    source_unit_id=unit_by_key[compiled.source_unit_key].id,
                    target_unit_id=unit_by_key[compiled.target_unit_key].id,
                    handoff_definition_key=handoff_ref.key if handoff_ref else None,
                    handoff_definition_version=handoff_ref.version if handoff_ref else None,
                    dependency_policy=compiled.dependency_policy,
                    escalation_policy=compiled.escalation_policy,
                )
            )
        self._fault_injector("relations")

        snapshot_payload = {
            "organization_id": plan.organization_id,
            "definition_revision": plan.definition_revision,
            "plan_digest": plan.plan_digest,
            "units": [item.model_dump(mode="json") for item in plan.units],
            "role_slots": [item.model_dump(mode="json") for item in plan.role_slots],
            "relations": [item.model_dump(mode="json") for item in plan.relations],
            # Bundle v2 needs the exact, digest-bound plan. Reconstructing it
            # later from normalized rows would silently lose policy bindings.
            "compiled_plan": plan.model_dump(mode="json"),
        }
        snapshot_hash = canonical_sha256(snapshot_payload)
        uow.snapshots.add(
            OrganizationTopologySnapshotDB(
                tenant_id=plan.tenant_id,
                project_id=plan.project_id,
                organization_id=plan.organization_id,
                revision=1,
                definition_revision=plan.definition_revision,
                snapshot_hash=snapshot_hash,
                snapshot_json=snapshot_payload,
            )
        )
        self._fault_injector("snapshot")

        uow.audit_outbox.add(
            OrganizationAuditOutboxDB(
                tenant_id=plan.tenant_id,
                project_id=plan.project_id,
                organization_id=plan.organization_id,
                event_key=f"organization-instantiated:{operation.operation_id}",
                event_kind="organization.instantiated.v1",
                payload_json={
                    "organization_id": plan.organization_id,
                    "plan_digest": plan.plan_digest,
                    "definition_revision": plan.definition_revision,
                    "principal_id": principal_id,
                },
            )
        )
        result = OrganizationInstantiationResult(
            organization_id=plan.organization_id,
            definition_revision=plan.definition_revision,
            plan_digest=plan.plan_digest,
            topology_snapshot_hash=snapshot_hash,
            team_ids=team_ids,
            unit_ids=[item.planned_id for item in plan.units],
            role_slot_ids=[item.planned_id for item in plan.role_slots],
            relation_ids=[item.planned_id for item in plan.relations],
            organization_admin_grant_id=instance_admin_grant.grant_id,
        )
        operation.status = "applied"
        operation.result_ref = plan.organization_id
        operation.result_json = {
            "schema": _INSTANTIATION_RECEIPT_SCHEMA,
            "precreation_admin_grant_id": str(authorization_ref or "").strip() or None,
            "result": result.model_dump(mode="json"),
        }
        operation.applied_at = time.time()
        uow.operations.add(operation)
        self._fault_injector("audit_outbox")
        return result

    @staticmethod
    def _enforce_concrete_limits(plan, limits) -> None:
        checks = (
            (plan.requested_team_count, limits.max_team_instances_per_organization, "organization_team_limit_exceeded"),
            (len(plan.units), limits.max_units_per_organization, "organization_unit_limit_exceeded"),
            (len(plan.role_slots), limits.max_role_slots_per_organization, "organization_role_slot_limit_exceeded"),
            (
                int(plan.expected_counts.get("assignment_capacity_default", 0)),
                limits.max_assignments_per_organization,
                "organization_assignment_limit_exceeded",
            ),
            (len(plan.relations), limits.max_relations_per_organization, "organization_relation_limit_exceeded"),
            (
                int(plan.expected_counts.get("workflow_step", 0)),
                limits.max_workflow_steps_per_organization,
                "organization_workflow_step_limit_exceeded",
            ),
        )
        for actual, limit, reason_code in checks:
            if actual > limit:
                raise OrganizationInstantiationError(reason_code)


def _topological_units(plan: CompiledOrganizationPlan):
    by_key = {item.unit_key: item for item in plan.units}
    emitted: set[str] = set()
    ordered = []
    while len(ordered) < len(plan.units):
        ready = sorted(
            (
                item
                for item in plan.units
                if item.unit_key not in emitted and (item.parent_unit_key is None or item.parent_unit_key in emitted)
            ),
            key=lambda item: item.unit_key,
        )
        if not ready:
            missing = sorted(set(by_key) - emitted)
            raise OrganizationInstantiationError(f"organization_unit_order_invalid:{','.join(missing)}")
        for item in ready:
            emitted.add(item.unit_key)
            ordered.append(item)
    return ordered


__all__ = ["OrganizationBlueprintInstantiationService", "OrganizationInstantiationError"]
