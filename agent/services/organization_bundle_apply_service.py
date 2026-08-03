"""Atomic application of a bound Organization Bundle v2 dry-run plan."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable

from sqlalchemy import func, update
from sqlmodel import Session, select

from agent.db_models.agents import AgentInfoDB
from agent.db_models.organizations import (
    OrganizationAdmissionExceptionDB,
    OrganizationAuditOutboxDB,
    OrganizationBlueprintRevisionDB,
    OrganizationHandoffDefinitionRevisionDB,
    OrganizationInstanceDB,
    OrganizationLimitProfileRevisionDB,
    OrganizationOperationDB,
    OrganizationPolicyRevisionDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationUnitDB,
    RoleTemplateRevisionDB,
    TeamBlueprintRevisionDB,
    WorkflowDefinitionRevisionDB,
)
from agent.db_models.projects import ProjectDB
from agent.models.organization_models import (
    AssignmentPolicyDefinition,
    OrganizationBundleImportPlan,
    OrganizationLimitProfile,
    SeparationOfDutiesDefinition,
    VersionedDefinitionRef,
    canonical_definition_sha256,
    canonical_json,
    canonical_sha256,
)
from agent.models.team_models import OrganizationBlueprintBundleV2
from agent.ports.organization_definitions import OrganizationLimitProfilePort
from agent.repositories.organizations.adapters import SqlOrganizationLimitProfileAdapter
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)
from agent.services.organization_blueprint_instantiation_service import (
    OrganizationBlueprintInstantiationService,
    OrganizationInstantiationError,
)
from agent.services.organization_bundle_service import (
    SECTION_METHODS,
    find_forbidden_portability_metadata_paths,
)
from agent.services.organization_custom_composition_service import (
    custom_composition_digest,
)
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
)
from agent.services.organization_slot_separation_service import (
    OrganizationSlotSeparationPolicy,
    evaluate_organization_slot_separation,
)
from agent.services.organization_template_security_service import (
    OrganizationTemplateSecurityService,
    installed_template_appendix_refs,
)
from agent.services.organization_unit_of_work import OrganizationUnitOfWork
from agent.services.project_plan_grant_service import (
    ProjectPlanGrantError,
    ProjectPlanGrantService,
)

_PORTABLE_PLAN_SECTIONS = frozenset({*SECTION_METHODS, "organization_instances", "assignments"})


class OrganizationBundleApplyError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class OrganizationBundleApplyService:
    def __init__(
        self,
        *,
        limit_profiles: OrganizationLimitProfilePort,
        uow_factory: Callable[[], OrganizationUnitOfWork] = OrganizationUnitOfWork,
        fault_injector: Callable[[str], None] | None = None,
        catalog=None,
        plan_grants: ProjectPlanGrantService | None = None,
        template_security: OrganizationTemplateSecurityService | None = None,
    ) -> None:
        self._limit_profiles = limit_profiles
        self._uow_factory = uow_factory
        self._fault_injector = fault_injector or (lambda _step: None)
        self._catalog = catalog
        self._plan_grants = plan_grants or ProjectPlanGrantService()
        self._template_security = template_security or OrganizationTemplateSecurityService()

    def apply(
        self,
        *,
        bundle: OrganizationBlueprintBundleV2,
        plan: OrganizationBundleImportPlan,
        idempotency_key: str,
        current_target_revision: str,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        admin_grant_id: str,
    ) -> dict:
        if find_forbidden_portability_metadata_paths(bundle.bundle_metadata):
            raise OrganizationBundleApplyError("organization_bundle_source_scope_metadata_forbidden")
        if any(
            any(
                value is not None
                for value in (
                    recipe.organization_id,
                    recipe.definition_revision,
                    recipe.effective_limit_profile_ref,
                    recipe.effective_limit_profile_revision,
                    recipe.effective_limit_profile_hash,
                    recipe.plan_digest,
                    recipe.topology_snapshot,
                )
            )
            for recipe in bundle.organization_instances
        ):
            raise OrganizationBundleApplyError("organization_bundle_source_bound_instance_forbidden")
        if any(value.organization_id is not None for value in bundle.assignments):
            raise OrganizationBundleApplyError("organization_bundle_source_bound_assignment_forbidden")
        if any(item.section not in _PORTABLE_PLAN_SECTIONS for item in plan.items):
            raise OrganizationBundleApplyError("organization_bundle_plan_section_forbidden")
        allowed_appendices = installed_template_appendix_refs(self._catalog)
        for revision in bundle.role_templates:
            decision = self._template_security.validate_role_definition(
                template_key=revision.key,
                template_version=revision.version,
                definition=revision.definition,
                allowed_appendix_refs=allowed_appendices,
            )
            if not decision.allowed:
                raise OrganizationBundleApplyError("organization_bundle_role_template_untrusted")
        plan_payload = plan.model_dump(mode="json", exclude={"plan_digest"})
        if canonical_sha256(plan_payload) != plan.plan_digest:
            raise OrganizationBundleApplyError("organization_bundle_plan_digest_invalid")
        if canonical_sha256(bundle.model_dump(mode="json")) != plan.bundle_digest:
            raise OrganizationBundleApplyError("organization_bundle_digest_stale")
        if plan.tenant_id != tenant_id or plan.project_id != project_id or plan.principal_id != principal_id:
            raise OrganizationBundleApplyError("organization_bundle_scope_mismatch")
        if plan.expires_at_epoch < time.time():
            raise OrganizationBundleApplyError("organization_bundle_plan_expired")
        if plan.errors or any(item.action == "conflict" for item in plan.items):
            raise OrganizationBundleApplyError("organization_bundle_plan_blocked")
        if not idempotency_key or not admin_grant_id:
            raise OrganizationBundleApplyError("organization_bundle_apply_binding_required")

        request_digest = canonical_sha256(
            {
                "bundle_digest": plan.bundle_digest,
                "plan_digest": plan.plan_digest,
                "idempotency_key": idempotency_key,
                "principal_id": principal_id,
                "admin_grant_id": admin_grant_id,
            }
        )
        result: dict | None = None
        with self._uow_factory() as uow:
            if self._catalog is not None:
                uow.definitions = FileCatalogDefinitionRepositoryAdapter(
                    uow.definitions,
                    self._catalog,
                    uow.session,
                )
            existing_operation = uow.operations.get_by_idempotency_key(
                plan.tenant_id,
                plan.project_id,
                "bundle_apply",
                idempotency_key,
                for_update=True,
            )
            if existing_operation is not None:
                if (
                    existing_operation.request_digest != request_digest
                    or existing_operation.plan_digest != plan.plan_digest
                ):
                    raise OrganizationBundleApplyError("organization_bundle_idempotency_conflict")
                if existing_operation.status != "applied":
                    raise OrganizationBundleApplyError("organization_bundle_apply_in_progress")
                if not existing_operation.result_json:
                    raise OrganizationBundleApplyError("organization_bundle_apply_result_missing")
                result = {**existing_operation.result_json, "idempotent_replay": True}
            else:
                try:
                    self._plan_grants.consume_in_session(
                        uow.session,
                        grant_id=admin_grant_id,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        principal_id=principal_id,
                        plan_digest=plan.plan_digest,
                        policy_hash=plan.effective_limit_profile_hash,
                        grant_kind="bundle_import",
                    )
                except ProjectPlanGrantError as exc:
                    raise OrganizationBundleApplyError("organization_bundle_admin_grant_invalid") from exc
                project_row = uow.session.exec(
                    select(ProjectDB)
                    .where(
                        ProjectDB.tenant_id == tenant_id,
                        ProjectDB.project_id == project_id,
                    )
                    .with_for_update()
                ).one_or_none()
                if project_row is None:
                    raise OrganizationBundleApplyError("organization_bundle_project_scope_missing")
                actual_target_revision = organization_bundle_target_revision(
                    uow.session,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    for_update=True,
                    catalog=self._catalog,
                )
                if (
                    current_target_revision != plan.expected_target_revision
                    or actual_target_revision != plan.expected_target_revision
                ):
                    raise OrganizationBundleApplyError("organization_bundle_target_revision_stale")
                result = self._stage_apply(
                    uow=uow,
                    bundle=bundle,
                    plan=plan,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                )
        if result is None:
            raise OrganizationBundleApplyError("organization_bundle_apply_result_missing")
        return result

    def _stage_apply(self, *, uow, bundle, plan, idempotency_key, request_digest):
        transaction_limit_profiles = SqlOrganizationLimitProfileAdapter(uow.definitions)
        current_limits = transaction_limit_profiles.resolve_limit_profile(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            policy_ref=plan.effective_limit_profile_ref,
        )
        if (
            current_limits.revision != plan.effective_limit_profile_revision
            or current_limits.content_hash() != plan.effective_limit_profile_hash
        ):
            raise OrganizationBundleApplyError("organization_bundle_limit_profile_stale")
        if len(canonical_json(bundle.model_dump(mode="json")).encode("utf-8")) > current_limits.max_bundle_bytes:
            raise OrganizationBundleApplyError("organization_bundle_size_limit_exceeded")

        operation = OrganizationOperationDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            operation_kind="bundle_apply",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            plan_digest=plan.plan_digest,
            expected_revision=plan.expected_target_revision,
            status="pending",
        )
        uow.operations.add(operation)
        self._fault_injector("operation")

        item_by_identity = {(item.section, item.key, item.version): item for item in plan.items}
        sections = (
            "policies",
            "limit_profiles",
            "role_templates",
            "handoff_definitions",
            "workflow_definitions",
            "team_blueprints",
            "organization_blueprints",
        )
        applied_items = 0
        for section in sections:
            for index, revision in enumerate(getattr(bundle, section)):
                item = item_by_identity[(section, revision.key, revision.version)]
                if item.action in {"skip", "unchanged"}:
                    continue
                existing = _get_existing(uow.definitions, section, plan, revision.key, revision.version)
                row = _definition_row(section, revision, plan, existing=existing)
                uow.definitions.add(row)
                applied_items += 1
                self._fault_injector(f"{section}:{index}")
        uow.flush()

        compiled_by_organization_id = {value.organization_id: value for value in plan.instance_plans}
        instance_results: list[dict] = []
        for index, recipe in enumerate(bundle.organization_instances):
            item = item_by_identity.get(("organization_instances", recipe.instance_key, 1))
            if item is None or item.action in {"skip", "unchanged"}:
                continue
            target_organization_id = plan.instance_organization_ids.get(recipe.instance_key)
            compiled = compiled_by_organization_id.get(str(target_organization_id or ""))
            if compiled is None:
                raise OrganizationBundleApplyError("organization_bundle_instance_plan_missing")
            if compiled.tenant_id != plan.tenant_id or compiled.project_id != plan.project_id:
                raise OrganizationBundleApplyError("organization_bundle_instance_scope_mismatch")
            if recipe.composition_mode == "custom":
                self._consume_custom_admission(
                    uow=uow,
                    plan=plan,
                    recipe=recipe,
                    compiled=compiled,
                )
            transaction_limits = SqlOrganizationLimitProfileAdapter(uow.definitions)
            try:
                result = OrganizationBlueprintInstantiationService(
                    limit_profiles=transaction_limits,
                    fault_injector=lambda step, ordinal=index: self._fault_injector(
                        f"organization_instances:{ordinal}:{step}"
                    ),
                ).stage_in_uow(
                    uow=uow,
                    plan=compiled,
                    name=plan.instance_names.get(recipe.instance_key, recipe.name),
                    idempotency_key=(
                        "bundle-instance-"
                        + hashlib.sha256(f"{idempotency_key}\0{recipe.instance_key}".encode("utf-8")).hexdigest()[:40]
                    ),
                    principal_id=plan.principal_id,
                )
            except OrganizationInstantiationError as exc:
                raise OrganizationBundleApplyError(exc.reason_code) from exc
            requested_lifecycle = plan.instance_requested_lifecycles.get(
                recipe.instance_key,
                "draft",
            )
            if requested_lifecycle == "validated":
                organization = uow.instances.get_scoped(
                    plan.tenant_id,
                    plan.project_id,
                    compiled.organization_id,
                    for_update=True,
                )
                if organization is None:
                    raise OrganizationBundleApplyError("organization_bundle_instance_result_missing")
                organization.lifecycle = "validated"
                organization.lock_version += 1
                organization.updated_at = time.time()
                uow.instances.add(organization)
            instance_results.append(
                {
                    "instance_key": recipe.instance_key,
                    "organization_id": result.organization_id,
                    "topology_snapshot_hash": result.topology_snapshot_hash,
                    "team_ids": list(result.team_ids),
                }
            )
            applied_items += 1
        uow.flush()

        applied_assignments = self._stage_assignments(
            uow=uow,
            bundle=bundle,
            plan=plan,
            item_by_identity=item_by_identity,
        )
        applied_items += applied_assignments
        uow.flush()

        uow.audit_outbox.add(
            OrganizationAuditOutboxDB(
                tenant_id=plan.tenant_id,
                project_id=plan.project_id,
                event_key=f"organization-bundle-applied:{operation.operation_id}",
                event_kind="organization.bundle_applied.v2",
                payload_json={
                    "bundle_digest": plan.bundle_digest,
                    "plan_digest": plan.plan_digest,
                    "principal_id": plan.principal_id,
                    "applied_items": applied_items,
                    "applied_instances": len(instance_results),
                    "applied_assignments": applied_assignments,
                },
            )
        )
        operation.status = "applied"
        operation.result_ref = plan.bundle_digest
        operation.applied_at = time.time()
        result = {
            "bundle_digest": plan.bundle_digest,
            "plan_digest": plan.plan_digest,
            "operation_id": operation.operation_id,
            "applied_items": applied_items,
            "instances": instance_results,
            "applied_assignments": applied_assignments,
            "idempotent_replay": False,
        }
        operation.result_json = result
        uow.operations.add(operation)
        self._fault_injector("audit_outbox")
        return result

    @staticmethod
    def _consume_custom_admission(*, uow, plan, recipe, compiled) -> None:
        exception_ref = str(plan.instance_admission_exception_refs.get(recipe.instance_key) or "").strip()
        if not exception_ref or not recipe.team_blueprint_counts:
            raise OrganizationBundleApplyError("organization_bundle_custom_admission_exception_required")
        definition = VersionedDefinitionRef.parse(compiled.definition_ref)
        composition_digest = custom_composition_digest(
            definition_ref=compiled.definition_ref,
            definition_revision=compiled.definition_revision,
            policy_hash=compiled.effective_limit_profile_hash,
            composition=dict(recipe.team_blueprint_counts),
        )
        now = time.time()
        result = uow.session.exec(
            update(OrganizationAdmissionExceptionDB)
            .where(OrganizationAdmissionExceptionDB.exception_id == exception_ref)
            .where(OrganizationAdmissionExceptionDB.tenant_id == plan.tenant_id)
            .where(OrganizationAdmissionExceptionDB.project_id == plan.project_id)
            .where(OrganizationAdmissionExceptionDB.principal_id == plan.principal_id)
            .where(OrganizationAdmissionExceptionDB.definition_key == definition.key)
            .where(OrganizationAdmissionExceptionDB.definition_version == definition.version)
            .where(OrganizationAdmissionExceptionDB.definition_revision == compiled.definition_revision)
            .where(OrganizationAdmissionExceptionDB.policy_hash == compiled.effective_limit_profile_hash)
            .where(OrganizationAdmissionExceptionDB.composition_digest == composition_digest)
            .where(OrganizationAdmissionExceptionDB.status == "issued")
            .where(OrganizationAdmissionExceptionDB.revoked_at.is_(None))
            .where(OrganizationAdmissionExceptionDB.expires_at > now)
            .values(
                status="consumed",
                consumed_at=now,
                consumed_organization_id=compiled.organization_id,
            )
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            raise OrganizationBundleApplyError("organization_bundle_custom_admission_exception_invalid")

    @staticmethod
    def _stage_assignments(*, uow, bundle, plan, item_by_identity) -> int:
        eligibility_service = OrganizationAssignmentEligibilityService()
        global_capacity: dict[str, int] = {}
        applied = 0
        for index, assignment in enumerate(bundle.assignments):
            identity = (
                assignment.instance_key,
                assignment.unit_key,
                assignment.role_slot_key,
                assignment.principal_ref,
            )
            item = item_by_identity.get(("assignments", ":".join(identity), 1))
            if item is None or item.action in {"skip", "unchanged"}:
                continue
            organization_id = plan.instance_organization_ids.get(assignment.instance_key)
            agent_url = plan.assignment_rebindings.get(assignment.principal_ref)
            if not organization_id or not agent_url:
                raise OrganizationBundleApplyError("organization_bundle_assignment_rebind_missing")
            unit = uow.session.exec(
                select(OrganizationUnitDB)
                .where(OrganizationUnitDB.tenant_id == plan.tenant_id)
                .where(OrganizationUnitDB.project_id == plan.project_id)
                .where(OrganizationUnitDB.organization_id == organization_id)
                .where(OrganizationUnitDB.unit_key == assignment.unit_key)
                .where(OrganizationUnitDB.lifecycle != "archived")
                .with_for_update()
            ).first()
            if unit is None:
                raise OrganizationBundleApplyError("organization_bundle_assignment_unit_missing")
            slot = uow.session.exec(
                select(OrganizationRoleSlotDB)
                .where(OrganizationRoleSlotDB.tenant_id == plan.tenant_id)
                .where(OrganizationRoleSlotDB.project_id == plan.project_id)
                .where(OrganizationRoleSlotDB.organization_id == organization_id)
                .where(OrganizationRoleSlotDB.unit_id == unit.id)
                .where(OrganizationRoleSlotDB.slot_key == assignment.role_slot_key)
                .where(OrganizationRoleSlotDB.lifecycle == "active")
                .with_for_update()
            ).first()
            if slot is None:
                raise OrganizationBundleApplyError("organization_bundle_assignment_role_slot_missing")
            agent = uow.session.get(AgentInfoDB, agent_url)
            policy = AssignmentPolicyDefinition.model_validate(dict(slot.assignment_policy or {}))
            if agent_url not in global_capacity:
                global_capacity[agent_url] = int(
                    uow.session.exec(
                        select(func.count(OrganizationRoleAssignmentDB.id)).where(
                            OrganizationRoleAssignmentDB.agent_url == agent_url,
                            OrganizationRoleAssignmentDB.lifecycle.in_(("proposed", "active")),
                        )
                    ).one()
                    or 0
                )
            eligibility = eligibility_service.evaluate(
                agent=agent,
                required_capabilities=set(policy.required_capabilities),
                forbidden_capabilities=set(policy.forbidden_capabilities),
                capacity_used=global_capacity[agent_url],
                principal_kind_allowed="agent" in policy.principal_kinds,
                write_access_required=policy.write_access_required,
            )
            if not eligibility.allowed:
                raise OrganizationBundleApplyError("organization_bundle_assignment_agent_ineligible")
            current_slot_count = int(
                uow.session.exec(
                    select(func.count(OrganizationRoleAssignmentDB.id)).where(
                        OrganizationRoleAssignmentDB.tenant_id == plan.tenant_id,
                        OrganizationRoleAssignmentDB.project_id == plan.project_id,
                        OrganizationRoleAssignmentDB.organization_id == organization_id,
                        OrganizationRoleAssignmentDB.role_slot_id == slot.id,
                        OrganizationRoleAssignmentDB.lifecycle.in_(("proposed", "active")),
                    )
                ).one()
                or 0
            )
            if slot.max_count is not None and current_slot_count >= int(slot.max_count):
                raise OrganizationBundleApplyError("organization_bundle_assignment_slot_capacity_exceeded")
            unit_slots = tuple(
                uow.session.exec(
                    select(OrganizationRoleSlotDB)
                    .where(OrganizationRoleSlotDB.tenant_id == plan.tenant_id)
                    .where(OrganizationRoleSlotDB.project_id == plan.project_id)
                    .where(OrganizationRoleSlotDB.organization_id == organization_id)
                    .where(OrganizationRoleSlotDB.unit_id == unit.id)
                    .where(OrganizationRoleSlotDB.lifecycle != "archived")
                ).all()
            )
            assigned_slot_ids = tuple(
                value.role_slot_id
                for value in uow.session.exec(
                    select(OrganizationRoleAssignmentDB).where(
                        OrganizationRoleAssignmentDB.tenant_id == plan.tenant_id,
                        OrganizationRoleAssignmentDB.project_id == plan.project_id,
                        OrganizationRoleAssignmentDB.organization_id == organization_id,
                        OrganizationRoleAssignmentDB.agent_url == agent_url,
                        OrganizationRoleAssignmentDB.lifecycle.in_(("proposed", "active")),
                    )
                ).all()
            )
            separation = evaluate_organization_slot_separation(
                target=OrganizationSlotSeparationPolicy(
                    slot_id=slot.id,
                    slot_key=slot.slot_key,
                    definition=SeparationOfDutiesDefinition.model_validate(dict(slot.separation_of_duties or {})),
                ),
                peers=(
                    OrganizationSlotSeparationPolicy(
                        slot_id=value.id,
                        slot_key=value.slot_key,
                        definition=SeparationOfDutiesDefinition.model_validate(dict(value.separation_of_duties or {})),
                    )
                    for value in unit_slots
                ),
                assigned_slot_ids=assigned_slot_ids,
                agent_capabilities=eligibility.capabilities,
            )
            if separation.has_conflict and separation.enforcement == "strict":
                raise OrganizationBundleApplyError("organization_bundle_assignment_sod_conflict")
            assignment_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"ananta:bundle-assignment:{organization_id}:{slot.id}:{agent_url}",
                )
            )
            uow.assignments.add(
                OrganizationRoleAssignmentDB(
                    id=assignment_id,
                    tenant_id=plan.tenant_id,
                    project_id=plan.project_id,
                    organization_id=organization_id,
                    role_slot_id=slot.id,
                    agent_url=agent_url,
                    lifecycle="proposed",
                    assignment_metadata={
                        "source": "organization_bundle_v2",
                        "bundle_plan_digest": plan.plan_digest,
                        "portable_principal_ref": assignment.principal_ref,
                        "principal_kind": "registered_worker",
                        "principal_id": agent_url,
                    },
                )
            )
            global_capacity[agent_url] += 1
            applied += 1
        return applied


def _get_existing(repository, section, plan, key, version):
    method = {
        "role_templates": repository.get_role_template,
        "team_blueprints": repository.get_team_blueprint,
        "workflow_definitions": repository.get_workflow,
        "organization_blueprints": repository.get_organization_blueprint,
        "handoff_definitions": repository.get_handoff,
        "policies": repository.get_policy,
        "limit_profiles": repository.get_limit_profile,
    }[section]
    return method(plan.tenant_id, plan.project_id, key, version)


def _definition_row(section, revision, plan, *, existing=None):
    definition = dict(revision.definition)
    if existing is not None:
        if getattr(existing, "lifecycle", "active") != "draft":
            raise OrganizationBundleApplyError("organization_bundle_active_revision_immutable")
        row = existing
    elif section == "role_templates":
        prompt = str(definition.get("prompt_template") or "")
        row = RoleTemplateRevisionDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            definition_key=revision.key,
            version=revision.version,
            content_hash=revision.content_hash,
            prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )
    elif section == "team_blueprints":
        workflow_ref = (
            VersionedDefinitionRef.parse(definition["workflow_ref"]) if definition.get("workflow_ref") else None
        )
        row = TeamBlueprintRevisionDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            definition_key=revision.key,
            version=revision.version,
            content_hash=revision.content_hash,
            workflow_definition_key=workflow_ref.key if workflow_ref else None,
            workflow_definition_version=workflow_ref.version if workflow_ref else None,
        )
    elif section == "workflow_definitions":
        row = WorkflowDefinitionRevisionDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            definition_key=revision.key,
            version=revision.version,
            content_hash=revision.content_hash,
            mode=str(definition.get("mode") or "gated"),
            default_failure_policy=str(definition.get("default_failure_policy") or "manual"),
        )
    elif section == "organization_blueprints":
        row = OrganizationBlueprintRevisionDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            definition_key=revision.key,
            version=revision.version,
            content_hash=revision.content_hash,
            limit_policy_ref=str(definition.get("limit_policy_ref") or ""),
        )
    elif section == "handoff_definitions":
        row = OrganizationHandoffDefinitionRevisionDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            definition_key=revision.key,
            version=revision.version,
            content_hash=revision.content_hash,
            required_artifact_kinds=list(definition.get("required_artifact_kinds") or []),
            acceptance_gate_ref=str(definition.get("acceptance_gate_ref") or ""),
        )
    elif section == "policies":
        row = OrganizationPolicyRevisionDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            policy_key=revision.key,
            revision=revision.version,
            content_hash=revision.content_hash,
        )
    elif section == "limit_profiles":
        profile = OrganizationLimitProfile.model_validate(definition)
        if profile.policy_id != revision.key or profile.revision != revision.version:
            raise OrganizationBundleApplyError("organization_bundle_limit_profile_identity_mismatch")
        row = OrganizationLimitProfileRevisionDB(
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            policy_key=revision.key,
            revision=revision.version,
            profile_hash=revision.content_hash,
            limits_json=profile.model_dump(mode="json", exclude={"policy_id", "revision"}),
        )
    else:
        raise OrganizationBundleApplyError("organization_bundle_section_unsupported")

    row.lifecycle = revision.lifecycle
    if hasattr(row, "definition_json"):
        row.definition_json = definition
    if hasattr(row, "content_hash"):
        row.content_hash = revision.content_hash
    if section == "role_templates":
        prompt = str(definition.get("prompt_template") or "")
        row.prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        row.appendix_refs = list(definition.get("appendix_refs") or [])
        row.metadata_json = dict(definition.get("metadata") or {})
    elif section == "workflow_definitions":
        row.mode = str(definition.get("mode") or "gated")
        row.default_failure_policy = str(definition.get("default_failure_policy") or "manual")
        row.steps_json = list(definition.get("steps") or [])
        row.checks_json = dict(definition.get("checks") or {})
        row.required_capabilities = list(definition.get("required_capabilities") or [])
    elif section == "team_blueprints":
        workflow_ref = (
            VersionedDefinitionRef.parse(definition["workflow_ref"]) if definition.get("workflow_ref") else None
        )
        row.workflow_definition_key = workflow_ref.key if workflow_ref else None
        row.workflow_definition_version = workflow_ref.version if workflow_ref else None
    elif section == "organization_blueprints":
        row.limit_policy_ref = str(definition.get("limit_policy_ref") or "")
    elif section == "handoff_definitions":
        row.required_artifact_kinds = list(definition.get("required_artifact_kinds") or [])
        row.acceptance_gate_ref = str(definition.get("acceptance_gate_ref") or "")
    elif section == "limit_profiles":
        profile = OrganizationLimitProfile.model_validate(definition)
        row.profile_hash = revision.content_hash
        row.limits_json = profile.model_dump(mode="json", exclude={"policy_id", "revision"})
    return row


def organization_bundle_target_revision(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    for_update: bool = False,
    catalog=None,
) -> str:
    """Hash the complete project-scoped import target without local IDs.

    The same function is used by preview and apply.  Apply locks every row it
    hashes so a concurrent catalog/instance write cannot slip between stale
    detection and the first staged import write.
    """

    definitions: list[dict] = []
    sections = (
        ("role_templates", RoleTemplateRevisionDB, "definition_key", "version", "content_hash"),
        ("team_blueprints", TeamBlueprintRevisionDB, "definition_key", "version", "content_hash"),
        ("workflow_definitions", WorkflowDefinitionRevisionDB, "definition_key", "version", "content_hash"),
        ("organization_blueprints", OrganizationBlueprintRevisionDB, "definition_key", "version", "content_hash"),
        ("handoff_definitions", OrganizationHandoffDefinitionRevisionDB, "definition_key", "version", "content_hash"),
        ("policies", OrganizationPolicyRevisionDB, "policy_key", "revision", "content_hash"),
        ("limit_profiles", OrganizationLimitProfileRevisionDB, "policy_key", "revision", "profile_hash"),
    )
    database_identities: set[tuple[str, str, int]] = set()
    for section, model, key_field, version_field, hash_field in sections:
        statement = select(model).where(model.tenant_id == tenant_id).where(model.project_id == project_id)
        if for_update:
            statement = statement.with_for_update()
        for row in session.exec(statement).all():
            key = str(getattr(row, key_field))
            version = int(getattr(row, version_field))
            database_identities.add((section, key, version))
            definitions.append(
                {
                    "section": section,
                    "key": key,
                    "version": version,
                    "content_hash": str(getattr(row, hash_field)),
                    "lifecycle": str(getattr(row, "lifecycle", "active")),
                }
            )

    if catalog is not None:
        snapshot = catalog.snapshot()
        catalog_sections = (
            ("role_templates", snapshot.role_templates),
            ("team_blueprints", snapshot.team_blueprints),
            ("workflow_definitions", snapshot.workflows),
            ("organization_blueprints", snapshot.organization_blueprints),
            ("handoff_definitions", snapshot.handoffs),
            ("policies", snapshot.policies),
            ("limit_profiles", snapshot.limit_profiles),
        )
        for section, values in catalog_sections:
            for (key, version), value in values.items():
                if (section, key, version) in database_identities:
                    continue
                payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
                content_hash = (
                    value.content_hash() if section == "limit_profiles" else canonical_definition_sha256(payload)
                )
                definitions.append(
                    {
                        "section": section,
                        "key": key,
                        "version": version,
                        "content_hash": content_hash,
                        "lifecycle": "active",
                    }
                )

    instance_statement = (
        select(OrganizationInstanceDB)
        .where(OrganizationInstanceDB.tenant_id == tenant_id)
        .where(OrganizationInstanceDB.project_id == project_id)
    )
    if for_update:
        instance_statement = instance_statement.with_for_update()
    instances = [
        {
            "organization_id": row.organization_id,
            "definition_revision": row.definition_revision,
            "plan_digest": row.plan_digest,
            "lifecycle": row.lifecycle,
            "lock_version": row.lock_version,
        }
        for row in session.exec(instance_statement).all()
    ]
    return canonical_sha256(
        {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "definitions": sorted(
                definitions,
                key=lambda item: (item["section"], item["key"], item["version"]),
            ),
            "instances": sorted(instances, key=lambda item: item["organization_id"]),
        }
    )


__all__ = [
    "OrganizationBundleApplyError",
    "OrganizationBundleApplyService",
    "organization_bundle_target_revision",
]
