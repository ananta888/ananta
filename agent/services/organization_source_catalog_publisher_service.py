"""Hub-owned materialization of Organization-scoped authoritative catalogs."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from agent.db_models import OrganizationAuditOutboxDB, OrganizationOperationDB, TaskDB
from agent.models.organization_source_catalog_models import (
    OrganizationSourceCatalogPublishCommand,
    OrganizationSourceCatalogPublishResult,
)
from agent.repositories.organization_source_catalog_repository import (
    OrganizationSourceCatalogPersistenceError,
    OrganizationSourceCatalogUniqueRaceError,
    OrganizationSourceCatalogUnitOfWork,
    OrganizationSourceCatalogUnitOfWorkPort,
    SourceCatalogPublishingAuthority,
)
from agent.services.hub_event_service import build_task_history_event
from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
    OrganizationMembershipService,
)
from agent.services.organization_source_catalog_binding_service import (
    OrganizationSourceCatalogBindingError,
    OrganizationSourceCatalogBindingService,
    canonical_sha256,
)
from agent.services.organization_source_catalog_query_adapter import (
    OrganizationSourceCatalogQueryError,
    OrganizationSourceCatalogQueryPort,
    OrganizationSourceCatalogQueryPrincipal,
)
from agent.services.source_catalog_service import SourceCatalogService

_ALLOWED_CREDENTIAL_TYPES = frozenset({"user", "service", "hub_service"})
_ALLOWED_PROJECT_ROLES = frozenset({"owner", "tenant_admin"})
_OPERATION_KIND = "organization_source_catalog_publish"
_MAX_RETRIEVED_CONTENT_CHARS = 2_000
_REPOSITORY_CONNECTORS = frozenset(
    {
        "registered_workspace",
        "local_directory",
        "git",
        "github",
        "generic_git",
        "github_repository",
    }
)


class OrganizationSourceCatalogPublisherError(RuntimeError):
    def __init__(self, reason_code: str, *, public_status: int) -> None:
        self.reason_code = str(reason_code)
        self.public_status = int(public_status)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class OrganizationSourceCatalogPublisherPrincipal:
    subject_id: str
    tenant_id: str
    project_id: str
    roles: frozenset[str]
    project_role: str
    credential_type: str = "user"

    def membership_principal(self) -> OrganizationAccessPrincipal:
        return OrganizationAccessPrincipal(
            principal_id=self.subject_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            credential_type=self.credential_type,
        )

    def query_principal(self) -> OrganizationSourceCatalogQueryPrincipal:
        return OrganizationSourceCatalogQueryPrincipal(
            subject_id=self.subject_id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            roles=self.roles,
            project_role=self.project_role,
        )


@dataclass(frozen=True, slots=True)
class _RetrievedRecord:
    record_file: str
    record_id: str | None
    path: str | None
    line_start: int | None
    line_end: int | None
    content_hash: str
    record_kind: str
    query_digests: tuple[str, ...]

    def locator(self) -> tuple[str, str, str, int | None, int | None]:
        return (
            self.record_file,
            self.record_id or "",
            self.path or "",
            self.line_start,
            self.line_end,
        )

    def canonical_key(self) -> tuple[Any, ...]:
        return (*self.locator(), self.content_hash)


class OrganizationSourceCatalogPublisherService:
    """Publish evidence identities without executing or dispatching Worker work."""

    def __init__(
        self,
        *,
        query_port: OrganizationSourceCatalogQueryPort,
        membership_service: OrganizationMembershipService | None = None,
        catalog_service: SourceCatalogService | None = None,
        binding_service: OrganizationSourceCatalogBindingService | None = None,
        uow_factory: Callable[[], OrganizationSourceCatalogUnitOfWorkPort]
        | None = None,
        clock: Callable[[], float] = time.time,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._query = query_port
        self._membership = membership_service or OrganizationMembershipService()
        self._catalogs = catalog_service or SourceCatalogService()
        self._bindings = binding_service or OrganizationSourceCatalogBindingService()
        self._uow_factory = uow_factory or OrganizationSourceCatalogUnitOfWork
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _step: None)

    def publish(
        self,
        *,
        principal: OrganizationSourceCatalogPublisherPrincipal,
        organization_id: str,
        command: OrganizationSourceCatalogPublishCommand,
        idempotency_key: str,
    ) -> OrganizationSourceCatalogPublishResult:
        self._validate_principal(principal)
        normalized_key = str(idempotency_key or "").strip()
        if not 8 <= len(normalized_key) <= 191 or any(
            character.isspace() for character in normalized_key
        ):
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_idempotency_key_invalid",
                public_status=400,
            )
        request_digest = canonical_sha256(
            {
                "tenant_id": principal.tenant_id,
                "project_id": principal.project_id,
                "organization_id": organization_id,
                "principal_id": principal.subject_id,
                "connection_id": command.connection_id,
                "query_digests": [self._digest_text(value) for value in command.queries],
                "query_limit": command.limit,
            }
        )
        replay = self._preflight_replay(
            principal=principal,
            organization_id=organization_id,
            idempotency_key=normalized_key,
            request_digest=request_digest,
        )
        if replay is not None:
            return replay

        batches = []
        for query in command.queries:
            try:
                batches.append(
                    (
                        self._digest_text(query),
                        self._query.query(
                            principal=principal.query_principal(),
                            connection_id=command.connection_id,
                            query=query,
                            limit=command.limit,
                        ),
                    )
                )
            except OrganizationSourceCatalogQueryError as exc:
                raise OrganizationSourceCatalogPublisherError(
                    exc.reason_code,
                    public_status=exc.public_status,
                ) from exc
        index_ids = {batch.knowledge_index_id for _, batch in batches}
        if len(index_ids) != 1:
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_active_index_changed",
                public_status=409,
            )
        expected_index_id = next(iter(index_ids))
        records = self._records_from_batches(batches)
        if not records:
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_no_matches",
                public_status=409,
            )

        try:
            with self._uow_factory() as uow:
                organization = self._authorize_in_uow(
                    uow,
                    principal=principal,
                    organization_id=organization_id,
                )
                existing = uow.operations.get_by_idempotency_key(
                    principal.tenant_id,
                    principal.project_id,
                    _OPERATION_KIND,
                    normalized_key,
                    for_update=True,
                )
                if existing is not None:
                    return self._replay(
                        uow,
                        operation=existing,
                        organization_id=organization_id,
                        request_digest=request_digest,
                    )
                authority = uow.catalogs.resolve_publishing_authority(
                    tenant_id=principal.tenant_id,
                    project_id=principal.project_id,
                    connection_id=command.connection_id,
                    expected_knowledge_index_id=expected_index_id,
                    for_update=True,
                )
                task_id = self._stable_id(
                    "source-catalog-task",
                    principal.tenant_id,
                    principal.project_id,
                    organization_id,
                    normalized_key,
                )
                if uow.catalogs.task_id_exists(task_id):
                    raise OrganizationSourceCatalogPublisherError(
                        "organization_source_catalog_task_identity_conflict",
                        public_status=409,
                    )
                catalog, publication = self._build_catalog(
                    task_id=task_id,
                    organization_id=organization_id,
                    authority=authority,
                    query_digests=[value for value, _batch in batches],
                    query_limit=command.limit,
                    records=records,
                )
                uow.catalogs.verify_bound_records(
                    authority=authority,
                    record_bindings=publication["record_bindings"],
                )
                self._fault_injector("records_verified")
                now = float(self._clock())
                task = self._catalog_task(
                    task_id=task_id,
                    principal=principal,
                    organization_id=organization_id,
                    catalog=catalog,
                    publication=publication,
                    now=now,
                )
                result = self._result(
                    organization_id=organization_id,
                    catalog=catalog,
                    authority=authority,
                    replayed=False,
                )
                operation = OrganizationOperationDB(
                    tenant_id=principal.tenant_id,
                    project_id=principal.project_id,
                    organization_id=organization_id,
                    operation_kind=_OPERATION_KIND,
                    idempotency_key=normalized_key,
                    request_digest=request_digest,
                    plan_digest=str(publication["binding_digest"]),
                    expected_revision=str(organization.definition_revision),
                    status="pending",
                )
                uow.catalogs.add_task(task)
                uow.operations.add(operation)
                self._fault_injector("task_and_operation")
                uow.audit_outbox.add(
                    OrganizationAuditOutboxDB(
                        tenant_id=principal.tenant_id,
                        project_id=principal.project_id,
                        organization_id=organization_id,
                        event_key=f"organization-source-catalog-published:{task_id}",
                        event_kind="organization.source_catalog_published.v1",
                        payload_json={
                            "catalog_task_id": task_id,
                            "catalog_id": catalog["catalog_id"],
                            "catalog_hash": catalog["catalog_hash"],
                            "organization_id": organization_id,
                            "principal_id": principal.subject_id,
                            "source_count": len(catalog["sources"]),
                            "query_count": len(command.queries),
                            "query_digest": canonical_sha256(
                                [value for value, _batch in batches]
                            ),
                            "publication_binding_digest": publication[
                                "binding_digest"
                            ],
                        },
                    )
                )
                operation.status = "applied"
                operation.result_ref = task_id
                operation.result_json = result.model_dump(mode="json")
                operation.applied_at = now
                uow.operations.add(operation)
                uow.flush()
                self._fault_injector("flushed")
                return result
        except OrganizationSourceCatalogUniqueRaceError:
            return self._replay_after_unique_race(
                principal=principal,
                organization_id=organization_id,
                idempotency_key=normalized_key,
                request_digest=request_digest,
            )
        except OrganizationSourceCatalogPersistenceError as exc:
            raise OrganizationSourceCatalogPublisherError(
                exc.reason_code,
                public_status=exc.public_status,
            ) from exc
        except OrganizationSourceCatalogBindingError as exc:
            raise OrganizationSourceCatalogPublisherError(
                exc.reason_code,
                public_status=409,
            ) from exc

    def _replay_after_unique_race(
        self,
        *,
        principal: OrganizationSourceCatalogPublisherPrincipal,
        organization_id: str,
        idempotency_key: str,
        request_digest: str,
    ) -> OrganizationSourceCatalogPublishResult:
        """Resolve the authoritative winner after a database uniqueness race."""

        with self._uow_factory() as uow:
            self._authorize_in_uow(
                uow,
                principal=principal,
                organization_id=organization_id,
            )
            operation = uow.operations.get_by_idempotency_key(
                principal.tenant_id,
                principal.project_id,
                _OPERATION_KIND,
                idempotency_key,
                for_update=True,
            )
            if operation is None:
                raise OrganizationSourceCatalogPublisherError(
                    "organization_source_catalog_concurrent_write_unresolved",
                    public_status=409,
                )
            return self._replay(
                uow,
                operation=operation,
                organization_id=organization_id,
                request_digest=request_digest,
            )

    def _preflight_replay(
        self,
        *,
        principal: OrganizationSourceCatalogPublisherPrincipal,
        organization_id: str,
        idempotency_key: str,
        request_digest: str,
    ) -> OrganizationSourceCatalogPublishResult | None:
        with self._uow_factory() as uow:
            self._authorize_in_uow(
                uow,
                principal=principal,
                organization_id=organization_id,
            )
            operation = uow.operations.get_by_idempotency_key(
                principal.tenant_id,
                principal.project_id,
                _OPERATION_KIND,
                idempotency_key,
                for_update=True,
            )
            if operation is None:
                return None
            return self._replay(
                uow,
                operation=operation,
                organization_id=organization_id,
                request_digest=request_digest,
            )

    def _authorize_in_uow(
        self,
        uow: OrganizationSourceCatalogUnitOfWorkPort,
        *,
        principal: OrganizationSourceCatalogPublisherPrincipal,
        organization_id: str,
    ):
        organization = uow.instances.get_scoped(
            principal.tenant_id,
            principal.project_id,
            organization_id,
            for_update=True,
        )
        if organization is None:
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_not_found",
                public_status=404,
            )
        membership = uow.memberships.get_for_principal(
            principal.tenant_id,
            principal.project_id,
            organization_id,
            principal.subject_id,
            for_update=True,
        )
        grants = uow.admin_grants.list_for_principal(
            principal.tenant_id,
            principal.project_id,
            organization_id,
            principal.subject_id,
            for_update=True,
        )
        if not self._membership.mutation_allowed(
            principal=principal.membership_principal(),
            tenant_id=principal.tenant_id,
            project_id=principal.project_id,
            organization_id=organization_id,
            grant_kind="planning:source_catalog_publish",
            membership=membership,
            grants=grants,
            now=float(self._clock()),
        ):
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_not_found",
                public_status=404,
            )
        if str(organization.lifecycle or "") != "active":
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_organization_not_active",
                public_status=409,
            )
        return organization

    def _replay(
        self,
        uow: OrganizationSourceCatalogUnitOfWorkPort,
        *,
        operation: Any,
        organization_id: str,
        request_digest: str,
    ) -> OrganizationSourceCatalogPublishResult:
        if str(operation.request_digest or "") != request_digest:
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_idempotency_conflict",
                public_status=409,
            )
        if str(operation.status or "") != "applied" or not operation.result_ref:
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_idempotency_in_progress",
                public_status=409,
            )
        task = uow.catalogs.get_task_scoped(
            tenant_id=str(operation.tenant_id),
            project_id=str(operation.project_id),
            organization_id=organization_id,
            task_id=str(operation.result_ref),
            for_update=True,
        )
        if task is None or task.task_kind != "source_catalog" or task.status != "completed":
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_replay_corrupt",
                public_status=500,
            )
        try:
            result = OrganizationSourceCatalogPublishResult.model_validate(
                dict(operation.result_json or {})
            )
        except ValueError as exc:
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_replay_corrupt",
                public_status=500,
            ) from exc
        catalog = dict((task.verification_status or {}).get("source_catalog") or {})
        raw_publication = (task.verification_status or {}).get(
            "source_catalog_publication"
        )
        if not isinstance(raw_publication, Mapping):
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_replay_corrupt",
                public_status=500,
            )
        try:
            publication = self._bindings.validate(raw_publication)
        except OrganizationSourceCatalogBindingError as exc:
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_replay_corrupt",
                public_status=500,
            ) from exc
        source_count = len(list(catalog.get("sources") or []))
        if (
            result.catalog_task_id != task.id
            or result.organization_id != organization_id
            or result.catalog_id != str(catalog.get("source_catalog_id") or "")
            or result.catalog_hash != str(catalog.get("source_catalog_hash") or "")
            or result.repository_revision != publication["revision_digest"]
            or result.manifest_hash != publication["index_manifest_digest"]
            or result.source_allowlist_version != result.catalog_hash
            or result.source_scope != f"organization:{organization_id}"
            or result.source_count != source_count
            or publication["organization_id"] != organization_id
            or publication["source_count"] != source_count
            or str(operation.plan_digest or "") != publication["binding_digest"]
        ):
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_replay_corrupt",
                public_status=500,
            )
        return result.model_copy(update={"replayed": True})

    def _build_catalog(
        self,
        *,
        task_id: str,
        organization_id: str,
        authority: SourceCatalogPublishingAuthority,
        query_digests: list[str],
        query_limit: int,
        records: list[_RetrievedRecord],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        scope = f"organization:{organization_id}"
        selected: list[dict[str, Any]] = []
        record_bindings: list[dict[str, Any]] = []
        for ordinal, record in enumerate(records, start=1):
            source_id = f"SRC_{ordinal:04d}"
            record_binding = {
                "source_id": source_id,
                "record_file": record.record_file,
                "record_id": record.record_id,
                "path": record.path,
                "line_start": record.line_start,
                "line_end": record.line_end,
                "content_hash": record.content_hash,
            }
            provenance_digest = self._bindings.source_provenance_digest(
                organization_id=organization_id,
                authority=authority,
                source_id=source_id,
                record_binding=record_binding,
            )
            source_kind = self._source_kind(
                authority=authority,
                record_kind=record.record_kind,
            )
            selected.append(
                {
                    "source_id": source_id,
                    "source_version": authority.revision_digest,
                    "tenant_id": authority.tenant_id,
                    "scope": scope,
                    "provenance_digest": provenance_digest,
                    "engine": "knowledge_index",
                    "kind": source_kind,
                    "path": record.path,
                    "record_id": record.record_id,
                    "line_start": record.line_start,
                    "line_end": record.line_end,
                    "content_hash": record.content_hash,
                    "manifest_hash": authority.index_manifest_digest,
                    "sensitivity": self._catalog_sensitivity(authority.sensitivity),
                }
            )
            record_bindings.append(record_binding)
        context_hash = canonical_sha256(
            {
                "knowledge_index_id": authority.knowledge_index_id,
                "revision_digest": authority.revision_digest,
                "query_digests": sorted(query_digests),
                "records": record_bindings,
            }
        )
        trace_id = "catalog-trace-" + canonical_sha256(
            {
                "task_id": task_id,
                "context_hash": context_hash,
                "manifest_hash": authority.index_manifest_digest,
            }
        )[:24]
        catalog = self._catalogs.build_catalog(
            task_id=task_id,
            retrieval_payload={
                "selected": selected,
                "retrieval_trace": {
                    "trace_id": trace_id,
                    "context_hash": context_hash,
                    "manifest_hash": authority.index_manifest_digest,
                    "tenant_id": authority.tenant_id,
                    "scope": scope,
                },
            },
            llm_scope="local_only",
        )
        if (
            catalog.get("catalog_state") != "current"
            or catalog.get("rejected_candidates")
            or len(catalog.get("sources") or []) != len(records)
        ):
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_materialization_invalid",
                public_status=409,
            )
        publication = self._bindings.build(
            organization_id=organization_id,
            authority=authority,
            query_digests=query_digests,
            query_limit=query_limit,
            record_bindings=record_bindings,
        )
        return catalog, publication

    def _records_from_batches(self, batches: list[tuple[str, Any]]) -> list[_RetrievedRecord]:
        records: dict[tuple[Any, ...], _RetrievedRecord] = {}
        locator_hashes: dict[tuple[Any, ...], str] = {}
        for query_digest, batch in batches:
            for raw_match in batch.matches:
                match = dict(raw_match)
                raw_metadata = match.get("metadata")
                if not isinstance(raw_metadata, Mapping):
                    raise OrganizationSourceCatalogPublisherError(
                        "organization_source_catalog_query_record_invalid",
                        public_status=502,
                    )
                metadata = dict(raw_metadata)
                content = match.get("content")
                record_file = str(metadata.get("record_file") or "").strip()
                record_id = str(
                    metadata.get("record_id") or match.get("id") or ""
                ).strip() or None
                path = str(
                    metadata.get("repo_relative_path")
                    or match.get("path")
                    or ""
                ).strip() or None
                if (
                    not isinstance(content, str)
                    or not content
                    or len(content) > _MAX_RETRIEVED_CONTENT_CHARS
                    or not record_file
                ):
                    raise OrganizationSourceCatalogPublisherError(
                        "organization_source_catalog_query_record_invalid",
                        public_status=502,
                    )
                line_start = self._line(
                    metadata.get("line_start", metadata.get("start_line"))
                )
                line_end = self._line(
                    metadata.get("line_end", metadata.get("end_line"))
                )
                if line_start is not None and line_end is not None and line_end < line_start:
                    raise OrganizationSourceCatalogPublisherError(
                        "organization_source_catalog_query_record_invalid",
                        public_status=502,
                    )
                content_hash = self._digest_text(content)
                record = _RetrievedRecord(
                    record_file=record_file,
                    record_id=record_id,
                    path=path,
                    line_start=line_start,
                    line_end=line_end,
                    content_hash=content_hash,
                    record_kind=str(metadata.get("record_kind") or match.get("kind") or ""),
                    query_digests=(query_digest,),
                )
                locator = record.locator()
                previous_hash = locator_hashes.get(locator)
                if previous_hash is not None and previous_hash != content_hash:
                    raise OrganizationSourceCatalogPublisherError(
                        "organization_source_catalog_query_record_inconsistent",
                        public_status=502,
                    )
                locator_hashes[locator] = content_hash
                key = record.canonical_key()
                previous = records.get(key)
                if previous is None:
                    records[key] = record
                else:
                    records[key] = replace(
                        previous,
                        query_digests=tuple(
                            sorted(set(previous.query_digests) | {query_digest})
                        ),
                    )
        return sorted(records.values(), key=lambda item: item.canonical_key())

    @staticmethod
    def _catalog_task(
        *,
        task_id: str,
        principal: OrganizationSourceCatalogPublisherPrincipal,
        organization_id: str,
        catalog: Mapping[str, Any],
        publication: Mapping[str, Any],
        now: float,
    ) -> TaskDB:
        projection = {
            "schema": catalog.get("schema"),
            "source_catalog_id": catalog.get("catalog_id"),
            "source_catalog_hash": catalog.get("catalog_hash"),
            "catalog_state": catalog.get("catalog_state"),
            "source_count": len(list(catalog.get("sources") or [])),
            "rejected_count": len(list(catalog.get("rejected_candidates") or [])),
            "retrieval_trace_id": catalog.get("retrieval_trace_id"),
            "retrieval_context_hash": catalog.get("retrieval_context_hash"),
            "retrieval_manifest_hash": catalog.get("retrieval_manifest_hash"),
            "sources": list(catalog.get("sources") or []),
        }
        task = TaskDB(
            id=task_id,
            title="Organization Source Catalog publication",
            description="Hub-owned content-free evidence allowlist publication.",
            status="completed",
            priority="High",
            created_at=now,
            updated_at=now,
            tenant_id=principal.tenant_id,
            project_id=principal.project_id,
            organization_id=organization_id,
            task_kind="source_catalog",
            required_capabilities=[],
            worker_execution_context={},
            verification_spec={
                "schema": "organization_source_catalog_verification.v1",
                "content_free": True,
            },
            verification_status={
                "source_catalog": projection,
                "source_catalog_publication": dict(publication),
            },
        )
        task.history = [
            build_task_history_event(
                task,
                "task_ingested",
                actor=principal.subject_id,
                timestamp=now,
                details={
                    "source": "api",
                    "organization_id": organization_id,
                    "catalog_id": catalog.get("catalog_id"),
                    "catalog_hash": catalog.get("catalog_hash"),
                    "publication_binding_digest": publication.get("binding_digest"),
                },
            )
        ]
        return task

    @staticmethod
    def _result(
        *,
        organization_id: str,
        catalog: Mapping[str, Any],
        authority: SourceCatalogPublishingAuthority,
        replayed: bool,
    ) -> OrganizationSourceCatalogPublishResult:
        catalog_hash = str(catalog.get("catalog_hash") or "")
        return OrganizationSourceCatalogPublishResult(
            organization_id=organization_id,
            catalog_task_id=str(catalog.get("task_id") or ""),
            catalog_id=str(catalog.get("catalog_id") or ""),
            catalog_hash=catalog_hash,
            repository_revision=authority.revision_digest,
            manifest_hash=authority.index_manifest_digest,
            source_allowlist_version=catalog_hash,
            source_scope=f"organization:{organization_id}",
            source_count=len(list(catalog.get("sources") or [])),
            replayed=replayed,
        )

    @staticmethod
    def _source_kind(
        *, authority: SourceCatalogPublishingAuthority, record_kind: str
    ) -> str:
        if authority.connector_type in _REPOSITORY_CONNECTORS or authority.index_source_scope in {
            "repo",
            "repo_path",
            "repository",
        }:
            return "repo_file"
        if authority.index_source_scope == "wiki":
            return "wiki_chunk"
        return record_kind or "rag_chunk"

    @staticmethod
    def _catalog_sensitivity(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {
            "public",
            "internal",
            "internal_high",
            "secret",
            "credential",
            "security_sensitive",
        }:
            return normalized
        if normalized in {"confidential", "restricted"}:
            return "internal_high"
        return "internal"

    @staticmethod
    def _line(value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_query_record_invalid",
                public_status=502,
            )
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_query_record_invalid",
                public_status=502,
            ) from exc
        if normalized < 1:
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_query_record_invalid",
                public_status=502,
            )
        return normalized

    @staticmethod
    def _digest_text(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8", errors="strict")).hexdigest()

    @staticmethod
    def _stable_id(prefix: str, *values: str) -> str:
        return f"{prefix}-{hashlib.sha256(chr(0).join(values).encode('utf-8')).hexdigest()[:24]}"

    @staticmethod
    def _validate_principal(principal: OrganizationSourceCatalogPublisherPrincipal) -> None:
        if (
            not principal.subject_id
            or not principal.tenant_id
            or not principal.project_id
            or principal.project_role not in _ALLOWED_PROJECT_ROLES
            or str(principal.credential_type or "").lower()
            not in _ALLOWED_CREDENTIAL_TYPES
        ):
            raise OrganizationSourceCatalogPublisherError(
                "organization_source_catalog_credential_forbidden",
                public_status=403,
            )


__all__ = [
    "OrganizationSourceCatalogPublisherError",
    "OrganizationSourceCatalogPublisherPrincipal",
    "OrganizationSourceCatalogPublisherService",
]
