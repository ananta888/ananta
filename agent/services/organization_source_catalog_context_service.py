"""Exact catalog-bound context hydration for Organization research Tasks."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sqlmodel import Session, select

from agent.db_models import (
    ContextBundleDB,
    KnowledgeIndexDB,
    OrganizationAdminGrantDB,
    OrganizationMembershipDB,
    RetrievalRunDB,
    TaskDB,
)
from agent.repositories.organization_source_catalog_repository import (
    OrganizationSourceCatalogPersistenceError,
    SourceCatalogPublishingAuthority,
    SqlOrganizationSourceCatalogRepository,
)
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.knowledge_index_retrieval_service import (
    KnowledgeIndexRetrievalService,
)
from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
    OrganizationMembershipService,
)
from agent.services.organization_source_catalog_binding_service import (
    OrganizationSourceCatalogBindingError,
    OrganizationSourceCatalogBindingService,
    canonical_sha256,
)
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
)
from agent.services.source_catalog_authority_service import (
    ResolvedSourceCatalog,
    SourceCatalogAuthorityService,
)

_CATALOG_TASK_SOURCES = frozenset({"agent", "api", "system", "ui"})
_CATALOG_TASK_KINDS = frozenset(
    {"knowledge", "planning_research", "research", "retrieval", "source_catalog"}
)


class OrganizationSourceCatalogContextError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class MaterializedOrganizationSourceCatalogContext:
    resolved_catalog: ResolvedSourceCatalog
    source_catalog: Mapping[str, Any]
    retrieval_run: RetrievalRunDB
    context_bundle: ContextBundleDB


class OrganizationSourceCatalogContextPort(Protocol):
    def materialize(
        self,
        session: Session,
        *,
        context: PlanningOperationContext,
        catalog_binding: Mapping[str, Any],
        task_id: str,
        goal_id: str,
    ) -> MaterializedOrganizationSourceCatalogContext: ...


class _LockedTaskRepository:
    def __init__(self, task: TaskDB) -> None:
        self._task = task

    def get_by_id(self, task_id: str) -> TaskDB | None:
        return self._task if task_id == self._task.id else None


class OrganizationSourceCatalogContextService:
    """Re-read exact bound records and write only a task-owned ContextBundle."""

    def __init__(
        self,
        *,
        binding_service: OrganizationSourceCatalogBindingService | None = None,
        record_reader: KnowledgeIndexRetrievalService | None = None,
    ) -> None:
        self._bindings = binding_service or OrganizationSourceCatalogBindingService()
        self._records = record_reader or KnowledgeIndexRetrievalService()

    def materialize(
        self,
        session: Session,
        *,
        context: PlanningOperationContext,
        catalog_binding: Mapping[str, Any],
        task_id: str,
        goal_id: str,
    ) -> MaterializedOrganizationSourceCatalogContext:
        self._require_current_research_authority(
            session,
            context=context,
        )
        catalog_task_id = str(catalog_binding.get("catalog_task_id") or "").strip()
        statement = select(TaskDB).where(
            TaskDB.id == catalog_task_id,
            TaskDB.tenant_id == context.tenant_id,
            TaskDB.project_id == context.project_id,
            TaskDB.organization_id == context.organization_id,
        )
        if self._supports_row_lock(session):
            statement = statement.with_for_update()
        catalog_task = session.exec(statement).one_or_none()
        if catalog_task is None:
            raise OrganizationSourceCatalogContextError(
                "category_research_source_catalog_not_found"
            )

        # Resolve from the exact locked row, then keep that immutable snapshot
        # for both catalog projection and context hydration.
        authority_service = SourceCatalogAuthorityService(
            _LockedTaskRepository(catalog_task)
        )
        source_scope = str(catalog_binding.get("source_scope") or "").strip()
        resolved = authority_service.resolve(
            principal=ChatSessionPrincipal.from_values(
                context.tenant_id,
                context.subject_id,
            ),
            catalog_task_id=catalog_task_id,
            catalog_id=str(catalog_binding.get("catalog_id") or ""),
            catalog_hash=str(catalog_binding.get("catalog_hash") or ""),
            repository_revision=str(
                catalog_binding.get("repository_revision") or ""
            ),
            manifest_hash=str(catalog_binding.get("manifest_hash") or ""),
            source_allowlist_version=str(
                catalog_binding.get("source_allowlist_version") or ""
            ),
            source_scope=source_scope,
            allowed_task_sources=_CATALOG_TASK_SOURCES,
            allowed_task_kinds=_CATALOG_TASK_KINDS,
            expected_task_tenant_id=context.tenant_id,
            expected_task_project_id=context.project_id,
            expected_task_organization_id=context.organization_id,
            organization_access_authorized=True,
        )
        verification = dict(catalog_task.verification_status or {})
        catalog = dict(verification.get("source_catalog") or {})
        raw_publication = verification.get("source_catalog_publication")
        if not isinstance(raw_publication, Mapping):
            raise OrganizationSourceCatalogContextError(
                "category_research_source_catalog_publication_missing"
            )
        try:
            publication = self._bindings.validate(raw_publication)
        except OrganizationSourceCatalogBindingError as exc:
            raise OrganizationSourceCatalogContextError(exc.reason_code) from exc
        if publication["organization_id"] != context.organization_id:
            raise OrganizationSourceCatalogContextError(
                "category_research_source_catalog_publication_scope_invalid"
            )
        repository = SqlOrganizationSourceCatalogRepository(session)
        try:
            source_authority = repository.resolve_publishing_authority(
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                connection_id=str(publication["connection_id"]),
                expected_knowledge_index_id=str(publication["knowledge_index_id"]),
                for_update=True,
            )
        except OrganizationSourceCatalogPersistenceError as exc:
            raise OrganizationSourceCatalogContextError(exc.reason_code) from exc
        self._require_same_lineage(publication, source_authority)
        sources = self._source_rows(
            catalog=catalog,
            resolved=resolved,
            publication=publication,
            authority=source_authority,
        )
        index = session.get(KnowledgeIndexDB, source_authority.knowledge_index_id)
        if index is None:
            raise OrganizationSourceCatalogContextError(
                "category_research_source_catalog_index_missing"
            )
        try:
            hydrated = self._records.load_bound_records(
                knowledge_index=index,
                bindings=[dict(item) for item in publication["record_bindings"]],
            )
        except ValueError as exc:
            raise OrganizationSourceCatalogContextError(str(exc)) from exc
        chunks = self._context_chunks(sources=sources, hydrated=hydrated)
        retrieval_run_id = self._stable_id(
            "catalog-retrieval",
            task_id,
            resolved.catalog_hash,
            str(publication["binding_digest"]),
        )
        context_bundle_id = self._stable_id(
            "catalog-context",
            task_id,
            resolved.catalog_hash,
            str(publication["binding_digest"]),
        )
        if session.get(RetrievalRunDB, retrieval_run_id) is not None or session.get(
            ContextBundleDB, context_bundle_id
        ) is not None:
            raise OrganizationSourceCatalogContextError(
                "category_research_context_identity_conflict"
            )
        context_text = "\n\n".join(
            f"[{row['metadata']['source_id']}] {row['source']}\n{row['content']}"
            for row in chunks
        )
        token_estimate = max(1, (len(context_text) + 3) // 4)
        retrieval_run = RetrievalRunDB(
            id=retrieval_run_id,
            query=f"source-catalog:{resolved.catalog_id}",
            task_id=task_id,
            goal_id=goal_id,
            strategy={
                "kind": "exact_source_catalog_binding",
                "search_performed": False,
            },
            chunk_count=len(chunks),
            token_estimate=token_estimate,
            policy_version="organization_source_catalog_context.v1",
            run_metadata={
                "catalog_task_id": resolved.catalog_task_id,
                "catalog_id": resolved.catalog_id,
                "catalog_hash": resolved.catalog_hash,
                "publication_binding_digest": publication["binding_digest"],
                "knowledge_index_id": publication["knowledge_index_id"],
                "source_revision_id": publication["source_revision_id"],
                "source_count": len(chunks),
                "content_free_metadata": True,
            },
        )
        context_bundle = ContextBundleDB(
            id=context_bundle_id,
            retrieval_run_id=retrieval_run_id,
            task_id=task_id,
            bundle_type="worker_execution_context",
            context_text=context_text,
            chunks=chunks,
            token_estimate=token_estimate,
            bundle_metadata={
                "schema": "organization_source_catalog_context.v1",
                "authority": "hub",
                "llm_scope": "local_only",
                "catalog_task_id": resolved.catalog_task_id,
                "catalog_id": resolved.catalog_id,
                "catalog_hash": resolved.catalog_hash,
                "repository_revision": resolved.repository_revision,
                "manifest_hash": resolved.manifest_hash,
                "source_allowlist_version": resolved.source_allowlist_version,
                "source_scope": source_scope,
                "publication_binding_digest": publication["binding_digest"],
                "content_location": "context_bundle_only",
            },
        )
        session.add(retrieval_run)
        session.add(context_bundle)
        return MaterializedOrganizationSourceCatalogContext(
            resolved_catalog=resolved,
            source_catalog={
                "schema": "source_catalog.v2",
                "source_catalog_id": resolved.catalog_id,
                "source_catalog_hash": resolved.catalog_hash,
                "sources": sources,
            },
            retrieval_run=retrieval_run,
            context_bundle=context_bundle,
        )

    @classmethod
    def _require_current_research_authority(
        cls,
        session: Session,
        *,
        context: PlanningOperationContext,
    ) -> None:
        """Revalidate the Organization grant in the Task-write transaction."""

        membership_statement = select(OrganizationMembershipDB).where(
            OrganizationMembershipDB.tenant_id == context.tenant_id,
            OrganizationMembershipDB.project_id == context.project_id,
            OrganizationMembershipDB.organization_id == context.organization_id,
            OrganizationMembershipDB.principal_id == context.subject_id,
        )
        grants_statement = select(OrganizationAdminGrantDB).where(
            OrganizationAdminGrantDB.tenant_id == context.tenant_id,
            OrganizationAdminGrantDB.project_id == context.project_id,
            OrganizationAdminGrantDB.organization_id == context.organization_id,
            OrganizationAdminGrantDB.principal_id == context.subject_id,
            OrganizationAdminGrantDB.revoked_at.is_(None),  # type: ignore[union-attr]
        )
        if cls._supports_row_lock(session):
            membership_statement = membership_statement.with_for_update()
            grants_statement = grants_statement.with_for_update()
        membership = session.exec(membership_statement).one_or_none()
        grants = list(session.exec(grants_statement).all())
        if not OrganizationMembershipService.mutation_allowed(
            principal=OrganizationAccessPrincipal(
                principal_id=context.subject_id,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
            ),
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            organization_id=context.organization_id,
            grant_kind="planning:category_research",
            membership=membership,
            grants=grants,
            now=time.time(),
        ):
            raise OrganizationSourceCatalogContextError(
                "category_research_source_catalog_authority_forbidden"
            )

    @staticmethod
    def _require_same_lineage(
        publication: Mapping[str, Any],
        authority: SourceCatalogPublishingAuthority,
    ) -> None:
        expected = {
            "connection_id": authority.connection_id,
            "source_revision_id": authority.source_revision_id,
            "revision_digest": authority.revision_digest,
            "source_manifest_digest": authority.source_manifest_digest,
            "admission_receipt_id": authority.admission_receipt_id,
            "admission_digest": authority.admission_digest,
            "knowledge_index_id": authority.knowledge_index_id,
            "index_run_id": authority.index_run_id,
            "index_source_scope": authority.index_source_scope,
            "index_manifest_digest": authority.index_manifest_digest,
            "policy_snapshot_digest": authority.policy_snapshot_digest,
            "active_generation": authority.active_generation,
        }
        if any(publication.get(field) != value for field, value in expected.items()):
            raise OrganizationSourceCatalogContextError(
                "category_research_source_catalog_lineage_stale"
            )

    @staticmethod
    def _source_rows(
        *,
        catalog: Mapping[str, Any],
        resolved: ResolvedSourceCatalog,
        publication: Mapping[str, Any],
        authority: SourceCatalogPublishingAuthority,
    ) -> list[dict[str, Any]]:
        raw_sources = catalog.get("sources")
        if not isinstance(raw_sources, list):
            raise OrganizationSourceCatalogContextError(
                "category_research_source_catalog_incomplete"
            )
        sources = [dict(row) for row in raw_sources if isinstance(row, Mapping)]
        source_by_id = {str(row.get("source_id") or ""): row for row in sources}
        binding_by_id = {
            str(row.get("source_id") or ""): dict(row)
            for row in publication["record_bindings"]
        }
        expected_ids = {reference.source_id for reference in resolved.source_refs}
        if (
            len(sources) != len(source_by_id)
            or set(source_by_id) != expected_ids
            or set(binding_by_id) != expected_ids
        ):
            raise OrganizationSourceCatalogContextError(
                "category_research_source_catalog_incomplete"
            )
        for source_id in expected_ids:
            source = source_by_id[source_id]
            binding = binding_by_id[source_id]
            if any(
                source.get(field) != binding.get(field)
                for field in (
                    "record_id",
                    "path",
                    "line_start",
                    "line_end",
                    "content_hash",
                )
            ):
                raise OrganizationSourceCatalogContextError(
                    "category_research_source_catalog_record_binding_mismatch"
                )
            expected_provenance = OrganizationSourceCatalogBindingService.source_provenance_digest(
                organization_id=str(publication["organization_id"]),
                authority=authority,
                source_id=source_id,
                record_binding=binding,
            )
            if (
                source.get("source_version") != authority.revision_digest
                or source.get("tenant_id") != authority.tenant_id
                or source.get("scope")
                != f"organization:{publication['organization_id']}"
                or source.get("manifest_hash") != authority.index_manifest_digest
                or source.get("task_id") != resolved.catalog_task_id
                or source.get("provenance_digest") != expected_provenance
            ):
                raise OrganizationSourceCatalogContextError(
                    "category_research_source_catalog_provenance_mismatch"
                )
        return [source_by_id[source_id] for source_id in sorted(expected_ids)]

    @staticmethod
    def _context_chunks(
        *,
        sources: list[dict[str, Any]],
        hydrated: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_by_id = {str(row["source_id"]): row for row in sources}
        hydrated_by_id = {str(row["source_id"]): row for row in hydrated}
        if set(source_by_id) != set(hydrated_by_id):
            raise OrganizationSourceCatalogContextError(
                "category_research_source_catalog_context_incomplete"
            )
        chunks: list[dict[str, Any]] = []
        for source_id in sorted(source_by_id):
            source = source_by_id[source_id]
            record = hydrated_by_id[source_id]
            content = str(record.get("content") or "")
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content_hash != source.get("content_hash"):
                raise OrganizationSourceCatalogContextError(
                    "category_research_source_catalog_content_mismatch"
                )
            source_type = OrganizationSourceCatalogContextService._runtime_source_type(
                str(source.get("source_type") or "")
            )
            sensitivity = str(source.get("sensitivity") or "internal")
            chunks.append(
                {
                    "engine": "organization_source_catalog",
                    "source": str(source.get("path") or source_id),
                    "content": content,
                    "score": 1.0,
                    "metadata": {
                        "source_type": source_type,
                        "source_origin": source_type,
                        "source_id": source_id,
                        "source_id_verified": True,
                        "source_id_verification": {
                            "status": "verified",
                            "reason_code": "hub_catalog_binding_verified",
                            "verified": True,
                        },
                        "source_ref": dict(source.get("source_ref") or {}),
                        "source_version": source.get("source_version"),
                        "tenant_id": source.get("tenant_id"),
                        "scope": source.get("scope"),
                        "provenance_digest": source.get("provenance_digest"),
                        "record_id": source.get("record_id"),
                        "record_file": record.get("record_file"),
                        "repo_relative_path": source.get("path"),
                        "line_start": source.get("line_start"),
                        "line_end": source.get("line_end"),
                        "content_hash": content_hash,
                        "source_manifest_hash": source.get("manifest_hash"),
                        "sensitivity": sensitivity,
                        "classification": (
                            "public"
                            if sensitivity == "public"
                            else "internal"
                            if sensitivity == "internal"
                            else "restricted"
                        ),
                        "citation": {
                            "source_id": source_id,
                            "source_type": source_type,
                            "verification_status": "verified",
                            "reason_code": "hub_catalog_binding_verified",
                        },
                    },
                }
            )
        return chunks

    @staticmethod
    def _runtime_source_type(source_type: str) -> str:
        return {
            "repo_file": "repo",
            "wiki_chunk": "wiki",
            "artifact": "artifact",
            "test_result": "artifact",
            "rag_chunk": "artifact",
        }.get(source_type, "artifact")

    @staticmethod
    def _stable_id(prefix: str, *values: str) -> str:
        digest = canonical_sha256(list(values))[:24]
        return f"{prefix}-{digest}"

    @staticmethod
    def _supports_row_lock(session: Session) -> bool:
        return (
            str(getattr(getattr(session.get_bind(), "dialect", None), "name", ""))
            == "postgresql"
        )


__all__ = [
    "MaterializedOrganizationSourceCatalogContext",
    "OrganizationSourceCatalogContextError",
    "OrganizationSourceCatalogContextPort",
    "OrganizationSourceCatalogContextService",
]
