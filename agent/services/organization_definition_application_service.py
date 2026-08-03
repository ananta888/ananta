"""Project-scoped Organization definition mutation and reconciliation.

Definitions are append-only content revisions.  Lifecycle markers may retire a
revision, but instance snapshots are never rewritten.  Every mutation is
previewed, digest-bound, authorized by a one-shot project grant, and committed
with its operation receipt and redacted audit event in one Unit of Work.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Mapping

from agent.db_models.organizations import (
    OrganizationAuditOutboxDB,
    OrganizationBlueprintRevisionDB,
    OrganizationOperationDB,
)
from agent.models.organization_models import (
    OrganizationBlueprintDefinition,
    VersionedDefinitionRef,
    canonical_definition_sha256,
    canonical_sha256,
)
from agent.repositories.organizations.adapters import (
    SqlOrganizationDefinitionCatalogAdapter,
    SqlOrganizationLimitProfileAdapter,
)
from agent.services.organization_blueprint_validation_service import (
    OrganizationBlueprintValidationService,
)
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
    OrganizationDefinitionCatalogService,
)
from agent.services.organization_reconciliation_service import (
    OrganizationReconciliationPlan,
    OrganizationReconciliationService,
)
from agent.services.organization_unit_of_work import OrganizationUnitOfWork
from agent.services.project_plan_grant_service import ProjectPlanGrantService


class OrganizationDefinitionMutationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class OrganizationDefinitionApplicationService:
    """Application boundary for immutable definition revisions."""

    def __init__(
        self,
        *,
        catalog: OrganizationDefinitionCatalogService,
        plan_grants: ProjectPlanGrantService | None = None,
        session_factory=None,
        clock=time.time,
        validator: OrganizationBlueprintValidationService | None = None,
        reconciler: OrganizationReconciliationService | None = None,
        uow_factory=OrganizationUnitOfWork,
    ) -> None:
        self._catalog = catalog
        self._session_factory = session_factory
        self._clock = clock
        self._plan_grants = plan_grants or ProjectPlanGrantService(
            session_factory=session_factory,
            clock=clock,
        )
        self._validator = validator or OrganizationBlueprintValidationService()
        self._reconciler = reconciler or OrganizationReconciliationService()
        self._uow_factory = uow_factory

    def validate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        definition_payload: Mapping[str, Any],
        lifecycle: str = "draft",
        expected_parent_revision: str | None = None,
    ) -> dict[str, Any]:
        definition = OrganizationBlueprintDefinition.model_validate(dict(definition_payload))
        normalized_lifecycle = self._lifecycle(lifecycle)
        with self._uow() as uow:
            definitions, limits = self._ports(uow, tenant_id=tenant_id, project_id=project_id)
            limit_profile = limits.resolve_limit_profile(
                tenant_id=tenant_id,
                project_id=project_id,
                policy_ref=definition.limit_policy_ref,
            )
            self._validator.ensure_valid(definition, catalog=definitions, limits=limit_profile)
            latest_version, latest_revision = self._latest_revision(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                key=definition.key,
            )
            if latest_version and definition.version != latest_version + 1:
                raise OrganizationDefinitionMutationError("organization_definition_version_not_next")
            if not latest_version and definition.version != 1:
                raise OrganizationDefinitionMutationError("organization_definition_initial_version_invalid")
            if expected_parent_revision is not None and expected_parent_revision != latest_revision:
                raise OrganizationDefinitionMutationError("organization_definition_parent_revision_stale")
            content_hash = canonical_definition_sha256(definition)
            policy_hash = limit_profile.content_hash()
            references = self._reference_hashes(definition, definitions=definitions)
            mutation_digest = self._mutation_digest(
                definition=definition,
                lifecycle=normalized_lifecycle,
                parent_revision=latest_revision,
                referenced_definition_hashes=references,
                policy_hash=policy_hash,
            )
        return {
            "definition_key": definition.key,
            "version": definition.version,
            "lifecycle": normalized_lifecycle,
            "revision": content_hash,
            "parent_revision": latest_revision,
            "mutation_digest": mutation_digest,
            "policy_hash": policy_hash,
            "referenced_definition_hashes": references,
            "diagnostics": [],
            "valid": True,
            "grant_kind": "definition_mutation",
        }

    def create_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        definition_payload: Mapping[str, Any],
        lifecycle: str,
        expected_parent_revision: str | None,
        mutation_digest: str,
        grant_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        definition = OrganizationBlueprintDefinition.model_validate(dict(definition_payload))
        normalized_lifecycle = self._lifecycle(lifecycle)
        request_digest = canonical_sha256(
            {
                "definition": definition.model_dump(mode="json"),
                "lifecycle": normalized_lifecycle,
                "expected_parent_revision": expected_parent_revision,
                "principal_id": principal_id,
            }
        )
        with self._uow() as uow:
            replay = self._operation_replay(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                operation_kind="definition_revision_create",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            definitions, limits = self._ports(uow, tenant_id=tenant_id, project_id=project_id)
            limit_profile = limits.resolve_limit_profile(
                tenant_id=tenant_id,
                project_id=project_id,
                policy_ref=definition.limit_policy_ref,
            )
            self._validator.ensure_valid(definition, catalog=definitions, limits=limit_profile)
            latest_version, parent_revision = self._latest_revision(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                key=definition.key,
                for_update=True,
            )
            if latest_version and definition.version != latest_version + 1:
                raise OrganizationDefinitionMutationError("organization_definition_version_not_next")
            if not latest_version and definition.version != 1:
                raise OrganizationDefinitionMutationError("organization_definition_initial_version_invalid")
            if expected_parent_revision != parent_revision:
                raise OrganizationDefinitionMutationError("organization_definition_parent_revision_stale")
            policy_hash = limit_profile.content_hash()
            references = self._reference_hashes(
                definition,
                definitions=definitions,
            )
            expected_mutation_digest = self._mutation_digest(
                definition=definition,
                lifecycle=normalized_lifecycle,
                parent_revision=parent_revision,
                referenced_definition_hashes=references,
                policy_hash=policy_hash,
            )
            if mutation_digest != expected_mutation_digest:
                raise OrganizationDefinitionMutationError("organization_definition_mutation_digest_mismatch")
            self._consume_grant(
                uow,
                grant_id=grant_id,
                tenant_id=tenant_id,
                project_id=project_id,
                principal_id=principal_id,
                grant_kind="definition_mutation",
                plan_digest=expected_mutation_digest,
                policy_hash=policy_hash,
            )
            now = self._clock()
            content_hash = canonical_definition_sha256(definition)
            row = OrganizationBlueprintRevisionDB(
                tenant_id=tenant_id,
                project_id=project_id,
                definition_key=definition.key,
                version=definition.version,
                lifecycle=normalized_lifecycle,
                content_hash=content_hash,
                limit_policy_ref=definition.limit_policy_ref,
                definition_json=definition.model_dump(mode="json"),
                referenced_definition_hashes=references,
                created_by=principal_id,
                created_at=now,
                activated_at=now if normalized_lifecycle == "active" else None,
            )
            uow.definitions.add(row)
            operation = OrganizationOperationDB(
                tenant_id=tenant_id,
                project_id=project_id,
                operation_kind="definition_revision_create",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                plan_digest=expected_mutation_digest,
                expected_revision=parent_revision,
                status="pending",
            )
            uow.operations.add(operation)
            result = {
                "definition_key": definition.key,
                "version": definition.version,
                "revision": content_hash,
                "lifecycle": normalized_lifecycle,
                "parent_revision": parent_revision,
                "mutation_digest": expected_mutation_digest,
                "replayed": False,
            }
            self._finish_operation(
                uow,
                operation=operation,
                event_kind="organization.definition_revision_created.v1",
                event_key=f"organization-definition-created:{operation.operation_id}",
                result=result,
                principal_id=principal_id,
                now=now,
            )
            return result

    def preview_archive(
        self,
        *,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int,
    ) -> dict[str, Any]:
        with self._uow() as uow:
            row = self._definition_row(uow, tenant_id, project_id, key, version)
            if row is None:
                raise OrganizationDefinitionMutationError("organization_blueprint_not_found")
            revision = str(row.content_hash)
            if canonical_definition_sha256(dict(row.definition_json or {})) != revision:
                raise OrganizationDefinitionMutationError("organization_definition_content_hash_mismatch")
            definition = OrganizationBlueprintDefinition.model_validate(dict(row.definition_json or {}))
            _definitions, limits = self._ports(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
            )
            limit_profile = limits.resolve_limit_profile(
                tenant_id=tenant_id,
                project_id=project_id,
                policy_ref=definition.limit_policy_ref,
            )
            digest = canonical_sha256({"action": "archive", "definition_ref": f"{key}@{version}", "revision": revision})
            active_instances = self._active_instance_ids(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                key=key,
                version=version,
            )
            already_retired = str(row.lifecycle) == "retired"
        return {
            "definition_ref": f"{key}@{version}",
            "revision": revision,
            "mutation_digest": digest,
            "policy_hash": limit_profile.content_hash(),
            "active_instance_ids": active_instances,
            "applicable": not active_instances and not already_retired,
            "blockers": (
                ["organization_definition_active_instances"]
                if active_instances
                else ["organization_definition_already_retired"]
                if already_retired
                else []
            ),
            "grant_kind": "definition_mutation",
        }

    def archive_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        key: str,
        version: int,
        expected_revision: str,
        mutation_digest: str,
        grant_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_digest = canonical_sha256(
            {
                "action": "archive",
                "definition_ref": f"{key}@{version}",
                "expected_revision": expected_revision,
                "principal_id": principal_id,
            }
        )
        with self._uow() as uow:
            replay = self._operation_replay(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                operation_kind="definition_revision_archive",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            row = self._definition_row(uow, tenant_id, project_id, key, version, for_update=True)
            if row is None:
                raise OrganizationDefinitionMutationError("organization_blueprint_not_found")
            if str(row.content_hash) != expected_revision:
                raise OrganizationDefinitionMutationError("organization_definition_revision_stale")
            if canonical_definition_sha256(dict(row.definition_json or {})) != expected_revision:
                raise OrganizationDefinitionMutationError("organization_definition_content_hash_mismatch")
            definition = OrganizationBlueprintDefinition.model_validate(dict(row.definition_json or {}))
            _definitions, limits = self._ports(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
            )
            limit_profile = limits.resolve_limit_profile(
                tenant_id=tenant_id,
                project_id=project_id,
                policy_ref=definition.limit_policy_ref,
            )
            if str(row.lifecycle) == "retired":
                raise OrganizationDefinitionMutationError("organization_definition_already_retired")
            expected_digest = canonical_sha256(
                {"action": "archive", "definition_ref": f"{key}@{version}", "revision": expected_revision}
            )
            if mutation_digest != expected_digest:
                raise OrganizationDefinitionMutationError("organization_definition_mutation_digest_mismatch")
            if self._active_instance_ids(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                key=key,
                version=version,
                for_update=True,
            ):
                raise OrganizationDefinitionMutationError("organization_definition_active_instances")
            self._consume_grant(
                uow,
                grant_id=grant_id,
                tenant_id=tenant_id,
                project_id=project_id,
                principal_id=principal_id,
                grant_kind="definition_mutation",
                plan_digest=expected_digest,
                policy_hash=limit_profile.content_hash(),
            )
            now = self._clock()
            persisted = uow.definitions.get_organization_blueprint(
                tenant_id,
                project_id,
                key,
                version,
                for_update=True,
            )
            if persisted is None:
                persisted = OrganizationBlueprintRevisionDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    definition_key=key,
                    version=version,
                    lifecycle="retired",
                    content_hash=row.content_hash,
                    limit_policy_ref=row.limit_policy_ref,
                    definition_json=dict(row.definition_json),
                    referenced_definition_hashes=self._reference_hashes(
                        definition,
                        definitions=self._catalog,
                    ),
                    created_by=principal_id,
                    created_at=now,
                )
            persisted.lifecycle = "retired"
            uow.definitions.add(persisted)
            operation = OrganizationOperationDB(
                tenant_id=tenant_id,
                project_id=project_id,
                operation_kind="definition_revision_archive",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                plan_digest=expected_digest,
                expected_revision=expected_revision,
                status="pending",
            )
            uow.operations.add(operation)
            result = {
                "definition_key": key,
                "version": version,
                "revision": expected_revision,
                "lifecycle": "retired",
                "mutation_digest": expected_digest,
                "replayed": False,
            }
            self._finish_operation(
                uow,
                operation=operation,
                event_kind="organization.definition_revision_retired.v1",
                event_key=f"organization-definition-retired:{operation.operation_id}",
                result=result,
                principal_id=principal_id,
                now=now,
            )
            return result

    def preview_reconcile(
        self,
        *,
        tenant_id: str,
        project_id: str,
        key: str,
        current_version: int,
        desired_definition: Mapping[str, Any],
        local_override_paths: tuple[str, ...],
    ) -> dict[str, Any]:
        desired = OrganizationBlueprintDefinition.model_validate(dict(desired_definition))
        if desired.key != key:
            raise OrganizationDefinitionMutationError("organization_definition_key_mismatch")
        override_paths = self._override_paths(local_override_paths)
        with self._uow() as uow:
            current = self._definition_row(uow, tenant_id, project_id, key, current_version)
            if current is None:
                raise OrganizationDefinitionMutationError("organization_blueprint_not_found")
            latest_version, latest_revision = self._latest_revision(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                key=key,
            )
            if current_version != latest_version or str(current.content_hash) != str(latest_revision):
                raise OrganizationDefinitionMutationError("organization_definition_revision_stale")
            definitions, limits = self._ports(uow, tenant_id=tenant_id, project_id=project_id)
            limit_profile = limits.resolve_limit_profile(
                tenant_id=tenant_id,
                project_id=project_id,
                policy_ref=desired.limit_policy_ref,
            )
            self._validator.ensure_valid(desired, catalog=definitions, limits=limit_profile)
            current_payload = dict(current.definition_json or {})
            current_content_hash = canonical_definition_sha256(current_payload)
            if current_content_hash != str(current.content_hash):
                raise OrganizationDefinitionMutationError("organization_definition_content_hash_mismatch")
            desired_content_hash = canonical_definition_sha256(desired)
            is_exact_noop = desired.version == current_version and desired_content_hash == current_content_hash
            if not is_exact_noop and desired.version != latest_version + 1:
                raise OrganizationDefinitionMutationError("organization_definition_version_not_next")
            snapshots = tuple(
                uow.definition_impacts.list_snapshot_hashes(
                    tenant_id,
                    project_id,
                    key,
                    current_version,
                )
            )
            assignment_links = uow.definition_impacts.list_assignment_links(
                tenant_id,
                project_id,
                key,
                current_version,
            )
            current_definition = OrganizationBlueprintDefinition.model_validate(current_payload)
            current_reference_hashes = dict(
                getattr(current, "referenced_definition_hashes", {}) or {}
            ) or self._reference_hashes(
                current_definition,
                definitions=definitions,
            )
            desired_reference_hashes = self._reference_hashes(
                desired,
                definitions=definitions,
            )
            plan = self._reconciler.plan(
                definition_key=key,
                current_definition=self._reconciliation_projection(
                    current_definition,
                    definitions=definitions,
                    reference_hashes=current_reference_hashes,
                ),
                desired_definition=self._reconciliation_projection(
                    desired,
                    definitions=definitions,
                    reference_hashes=desired_reference_hashes,
                ),
                current_revision=current_content_hash,
                desired_revision=desired_content_hash,
                local_override_paths=override_paths,
                active_instance_snapshot_revisions=snapshots,
                active_assignment_links=assignment_links,
            )
        return {
            **self._plan_payload(plan),
            "current_version": current_version,
            "desired_definition": desired.model_dump(mode="json"),
            "policy_hash": limit_profile.content_hash(),
            "grant_kind": "definition_reconcile",
        }

    def preview_seed_reconcile(
        self,
        *,
        tenant_id: str,
        project_id: str,
        key: str,
        current_version: int,
        local_override_paths: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Plan a project overlay revision from the immutable file seed."""

        seed = self._catalog.latest_organization_blueprint(key)
        if seed is None:
            raise OrganizationDefinitionMutationError("organization_seed_definition_not_found")
        desired_payload = seed.model_dump(mode="json")
        with self._uow() as uow:
            current = self._definition_row(
                uow,
                tenant_id,
                project_id,
                key,
                current_version,
            )
            if current is None:
                raise OrganizationDefinitionMutationError("organization_blueprint_not_found")
            exact_seed = str(current.content_hash) == canonical_definition_sha256(seed)
        if not exact_seed:
            desired_payload["version"] = current_version + 1
        result = self.preview_reconcile(
            tenant_id=tenant_id,
            project_id=project_id,
            key=key,
            current_version=current_version,
            desired_definition=desired_payload,
            local_override_paths=local_override_paths,
        )
        return {
            **result,
            "reconcile_source": "seed",
            "seed_source_ref": f"{seed.key}@{seed.version}",
            "seed_source_revision": canonical_definition_sha256(seed),
        }

    def apply_reconcile(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        preview: Mapping[str, Any],
        expected_revision: str,
        grant_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = str(preview.get("definition_key") or "")
        current_version = int(preview.get("current_version") or 0)
        overrides = tuple(preview.get("preserved_local_overrides") or ())
        source = str(preview.get("reconcile_source") or "payload")
        if source == "seed":
            recomputed = self.preview_seed_reconcile(
                tenant_id=tenant_id,
                project_id=project_id,
                key=key,
                current_version=current_version,
                local_override_paths=overrides,
            )
            if any(
                str(preview.get(field) or "") != str(recomputed.get(field) or "")
                for field in ("seed_source_ref", "seed_source_revision")
            ):
                raise OrganizationDefinitionMutationError("organization_reconcile_preview_stale")
        elif source == "payload":
            recomputed = self.preview_reconcile(
                tenant_id=tenant_id,
                project_id=project_id,
                key=key,
                current_version=current_version,
                desired_definition=dict(preview.get("desired_definition") or {}),
                local_override_paths=overrides,
            )
        else:
            raise OrganizationDefinitionMutationError("organization_reconcile_source_invalid")
        if str(preview.get("plan_digest") or "") != recomputed["plan_digest"]:
            raise OrganizationDefinitionMutationError("organization_reconcile_preview_stale")
        if recomputed["blockers"]:
            raise OrganizationDefinitionMutationError("organization_reconcile_blocked")
        if not recomputed["requires_apply"]:
            raise OrganizationDefinitionMutationError("organization_reconcile_no_changes")
        current_revision = str(recomputed.get("current_revision") or "")
        if expected_revision != current_revision:
            raise OrganizationDefinitionMutationError("organization_definition_revision_stale")
        desired = dict(recomputed.get("desired_definition") or {})
        return self._apply_reconcile_revision(
            tenant_id=tenant_id,
            project_id=project_id,
            principal_id=principal_id,
            desired=OrganizationBlueprintDefinition.model_validate(desired),
            current_version=current_version,
            current_revision=current_revision,
            plan_digest=recomputed["plan_digest"],
            policy_hash=recomputed["policy_hash"],
            grant_id=grant_id,
            idempotency_key=idempotency_key,
        )

    def _apply_reconcile_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        principal_id: str,
        desired: OrganizationBlueprintDefinition,
        current_version: int,
        current_revision: str,
        plan_digest: str,
        policy_hash: str,
        grant_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_digest = canonical_sha256(
            {
                "definition": desired.model_dump(mode="json"),
                "current_version": current_version,
                "current_revision": current_revision,
                "plan_digest": plan_digest,
                "principal_id": principal_id,
            }
        )
        with self._uow() as uow:
            replay = self._operation_replay(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                operation_kind="definition_reconcile_apply",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            current = self._definition_row(
                uow,
                tenant_id,
                project_id,
                desired.key,
                current_version,
                for_update=True,
            )
            if current is None or str(current.content_hash) != current_revision:
                raise OrganizationDefinitionMutationError("organization_definition_revision_stale")
            latest_version, latest_revision = self._latest_revision(
                uow,
                tenant_id=tenant_id,
                project_id=project_id,
                key=desired.key,
                for_update=True,
            )
            if latest_version != current_version or str(latest_revision) != current_revision:
                raise OrganizationDefinitionMutationError("organization_definition_revision_stale")
            if desired.version != current_version + 1:
                raise OrganizationDefinitionMutationError("organization_definition_version_not_next")
            definitions, limits = self._ports(uow, tenant_id=tenant_id, project_id=project_id)
            limit_profile = limits.resolve_limit_profile(
                tenant_id=tenant_id,
                project_id=project_id,
                policy_ref=desired.limit_policy_ref,
            )
            self._validator.ensure_valid(desired, catalog=definitions, limits=limit_profile)
            if limit_profile.content_hash() != policy_hash:
                raise OrganizationDefinitionMutationError("organization_reconcile_policy_stale")
            self._consume_grant(
                uow,
                grant_id=grant_id,
                tenant_id=tenant_id,
                project_id=project_id,
                principal_id=principal_id,
                grant_kind="definition_reconcile",
                plan_digest=plan_digest,
                policy_hash=policy_hash,
            )
            now = self._clock()
            row = OrganizationBlueprintRevisionDB(
                tenant_id=tenant_id,
                project_id=project_id,
                definition_key=desired.key,
                version=desired.version,
                lifecycle="active",
                content_hash=canonical_definition_sha256(desired),
                limit_policy_ref=desired.limit_policy_ref,
                definition_json=desired.model_dump(mode="json"),
                referenced_definition_hashes=self._reference_hashes(desired, definitions=definitions),
                created_by=principal_id,
                created_at=now,
                activated_at=now,
            )
            uow.definitions.add(row)
            operation = OrganizationOperationDB(
                tenant_id=tenant_id,
                project_id=project_id,
                operation_kind="definition_reconcile_apply",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                plan_digest=plan_digest,
                expected_revision=current_revision,
                status="pending",
            )
            uow.operations.add(operation)
            result = {
                "definition_key": desired.key,
                "version": desired.version,
                "revision": row.content_hash,
                "lifecycle": "active",
                "plan_digest": plan_digest,
                "preserved_instance_snapshots": True,
                "replayed": False,
            }
            self._finish_operation(
                uow,
                operation=operation,
                event_kind="organization.definition_reconciled.v1",
                event_key=f"organization-definition-reconciled:{operation.operation_id}",
                result=result,
                principal_id=principal_id,
                now=now,
            )
            return result

    def _ports(self, uow, *, tenant_id: str, project_id: str):
        repository = FileCatalogDefinitionRepositoryAdapter(
            uow.definitions,
            self._catalog,
            uow.session,
        )
        return (
            SqlOrganizationDefinitionCatalogAdapter(
                repository,
                tenant_id=tenant_id,
                project_id=project_id,
            ),
            SqlOrganizationLimitProfileAdapter(repository),
        )

    def _uow(self):
        return self._uow_factory(session_factory=self._session_factory)

    def _latest_revision(self, uow, *, tenant_id, project_id, key, for_update=False):
        rows = uow.definitions.list_organization_blueprint_revisions(
            tenant_id,
            project_id,
            key=key,
            for_update=for_update,
        )
        for row in rows:
            if canonical_definition_sha256(dict(row.definition_json or {})) != str(row.content_hash or ""):
                raise OrganizationDefinitionMutationError("organization_definition_content_hash_mismatch")
        candidates = [(row.version, row.content_hash) for row in rows]
        candidates.extend(
            (value.version, canonical_definition_sha256(value))
            for value in self._catalog.list_organization_blueprints()
            if value.key == key
        )
        return max(candidates, default=(0, None), key=lambda item: item[0])

    def _definition_row(self, uow, tenant_id, project_id, key, version, *, for_update=False):
        persisted = uow.definitions.get_organization_blueprint(
            tenant_id,
            project_id,
            key,
            version,
            for_update=for_update,
        )
        if persisted is not None:
            return persisted
        definition = self._catalog.get_organization_blueprint(key, version)
        if definition is None:
            return None
        payload = definition.model_dump(mode="json")
        return type(
            "CatalogDefinitionRow",
            (),
            {
                "content_hash": canonical_definition_sha256(definition),
                "definition_json": payload,
                "limit_policy_ref": definition.limit_policy_ref,
                "referenced_definition_hashes": {},
                "lifecycle": "active",
            },
        )()

    @staticmethod
    def _mutation_digest(
        *,
        definition,
        lifecycle,
        parent_revision,
        referenced_definition_hashes,
        policy_hash,
    ):
        return canonical_sha256(
            {
                "action": "create_definition_revision",
                "definition": definition.model_dump(mode="json"),
                "lifecycle": lifecycle,
                "parent_revision": parent_revision,
                "policy_hash": policy_hash,
                "referenced_definition_hashes": dict(sorted(referenced_definition_hashes.items())),
            }
        )

    @staticmethod
    def _lifecycle(value: str) -> str:
        lifecycle = str(value or "").strip().lower()
        if lifecycle not in {"draft", "active"}:
            raise OrganizationDefinitionMutationError("organization_definition_lifecycle_invalid")
        return lifecycle

    @staticmethod
    def _reference_hashes(definition, *, definitions) -> dict[str, str]:
        refs: set[str] = {definition.limit_policy_ref, definition.budgets.policy_ref}
        team_refs = {value for value in (unit.team_blueprint_ref for unit in definition.units) if value}
        team_refs.update(group.team_blueprint_ref for group in definition.unit_groups)
        refs.update(team_refs)
        refs.update(group.limit_policy_ref for group in definition.unit_groups)
        refs.update(value for value in (relation.handoff_contract_ref for relation in definition.relations) if value)
        for value in sorted(team_refs):
            team_ref = VersionedDefinitionRef.parse(value)
            team = definitions.get_team_blueprint(team_ref.key, team_ref.version)
            if team is None:
                continue
            refs.add(team.workflow_ref)
            refs.update(team.policies)
            for slot in team.role_slots:
                refs.add(slot.role_template_ref)
                refs.update(slot.overlays)
        result: dict[str, str] = {}
        for value in sorted(refs):
            content_hash = definitions.content_hash_for_ref(value)
            if content_hash is None:
                raise OrganizationDefinitionMutationError("organization_referenced_definition_hash_missing")
            result[value] = content_hash
        return result

    @staticmethod
    def _reconciliation_projection(
        definition: OrganizationBlueprintDefinition,
        *,
        definitions,
        reference_hashes: Mapping[str, str],
    ) -> dict[str, Any]:
        """Enrich an immutable definition for role/workflow/policy drift only."""

        payload = definition.model_dump(mode="json")
        team_refs = {
            value
            for value in (
                *(unit.team_blueprint_ref for unit in definition.units),
                *(group.team_blueprint_ref for group in definition.unit_groups),
            )
            if value
        }
        role_slots: list[dict[str, Any]] = []
        workflows: dict[str, dict[str, Any]] = {}
        policy_refs = {definition.limit_policy_ref, definition.budgets.policy_ref}
        policy_refs.update(group.limit_policy_ref for group in definition.unit_groups)
        for team_ref in sorted(team_refs):
            key, _separator, raw_version = team_ref.rpartition("@")
            team = definitions.get_team_blueprint(key, int(raw_version))
            if team is None:
                continue
            for slot in team.role_slots:
                item = slot.model_dump(mode="json")
                item["slot_id"] = f"{team_ref}:{slot.slot_id}"
                item["team_blueprint_ref"] = team_ref
                role_slots.append(item)
                policy_refs.update(slot.overlays)
            workflow_key, _separator, workflow_version = team.workflow_ref.rpartition("@")
            workflow = definitions.get_workflow_definition(
                workflow_key,
                int(workflow_version),
            )
            workflows[team.workflow_ref] = {
                "key": team.workflow_ref,
                "definition": workflow or {"unresolved": True},
            }
            policy_refs.update(team.policies)
        payload["role_slots"] = role_slots
        payload["workflows"] = [workflows[key] for key in sorted(workflows)]
        payload["policies"] = [
            {
                "key": value,
                "content_hash": definitions.content_hash_for_ref(value),
            }
            for value in sorted(policy_refs)
        ]
        payload["referenced_versions"] = dict(sorted(reference_hashes.items()))
        return payload

    @staticmethod
    def _operation_replay(
        uow,
        *,
        tenant_id,
        project_id,
        operation_kind,
        idempotency_key,
        request_digest,
    ):
        existing = uow.operations.get_by_idempotency_key(
            tenant_id,
            project_id,
            operation_kind,
            idempotency_key,
            for_update=True,
        )
        if existing is None:
            return None
        if existing.request_digest != request_digest:
            raise OrganizationDefinitionMutationError("organization_idempotency_key_conflict")
        if existing.status != "applied":
            raise OrganizationDefinitionMutationError("organization_definition_mutation_in_progress")
        return {**dict(existing.result_json or {}), "replayed": True}

    def _consume_grant(
        self,
        uow,
        *,
        grant_id,
        tenant_id,
        project_id,
        principal_id,
        grant_kind,
        plan_digest,
        policy_hash,
    ) -> None:
        self._plan_grants.consume_in_session(
            uow.session,
            grant_id=grant_id,
            tenant_id=tenant_id,
            project_id=project_id,
            principal_id=principal_id,
            grant_kind=grant_kind,
            plan_digest=plan_digest,
            policy_hash=policy_hash,
        )

    @staticmethod
    def _finish_operation(
        uow,
        *,
        operation,
        event_kind,
        event_key,
        result,
        principal_id,
        now,
    ) -> None:
        uow.audit_outbox.add(
            OrganizationAuditOutboxDB(
                tenant_id=operation.tenant_id,
                project_id=operation.project_id,
                organization_id=None,
                event_key=event_key,
                event_kind=event_kind,
                payload_json={**result, "principal_id": principal_id},
            )
        )
        operation.status = "applied"
        operation.result_ref = str(result.get("definition_key") or "")
        operation.result_json = result
        operation.applied_at = now
        uow.operations.add(operation)

    def _active_instance_ids(
        self,
        uow,
        *,
        tenant_id,
        project_id,
        key,
        version,
        for_update=False,
    ) -> list[str]:
        return uow.definition_impacts.list_active_instance_ids(
            tenant_id,
            project_id,
            key,
            version,
            for_update=for_update,
        )

    @staticmethod
    def _override_paths(values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            path = str(value or "").strip()
            if not path.startswith("$.") or len(path) > 512 or any(character.isspace() for character in path):
                raise OrganizationDefinitionMutationError("organization_local_override_path_invalid")
            normalized.append(path)
        return tuple(sorted(set(normalized)))

    @staticmethod
    def _plan_payload(plan: OrganizationReconciliationPlan) -> dict[str, Any]:
        return {
            "definition_key": plan.definition_key,
            "current_revision": plan.current_revision,
            "desired_revision": plan.desired_revision,
            "drift": [asdict(value) for value in plan.drift],
            "entity_drift": [asdict(value) for value in plan.entity_drift],
            "assignment_impacts": [asdict(value) for value in plan.assignment_impacts],
            "planned_writes": list(plan.planned_writes),
            "preserved_local_overrides": list(plan.preserved_local_overrides),
            "preserved_snapshot_revisions": list(plan.preserved_snapshot_revisions),
            "blockers": list(plan.blockers),
            "plan_digest": plan.plan_digest,
            "applicable": plan.applicable,
            "requires_apply": bool(plan.drift),
        }


__all__ = [
    "OrganizationDefinitionApplicationService",
    "OrganizationDefinitionMutationError",
]
