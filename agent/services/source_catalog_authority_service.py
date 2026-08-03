"""Fail-closed resolution of persisted Hub-owned source catalogs."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.source_catalog_service import (
    calculate_source_catalog_hash,
    calculate_source_catalog_id,
    validate_source_catalog_payload,
)
from ananta_contracts.retrieval import SourceRef


class SourceCatalogTaskRepositoryPort(Protocol):
    def get_by_id(self, task_id: str) -> Any | None: ...


class SourceCatalogAuthorityError(ValueError):
    """Stable fail-closed rejection at the persisted catalog boundary."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class ResolvedSourceCatalog:
    catalog_task_id: str
    catalog_id: str
    catalog_hash: str
    repository_revision: str
    manifest_hash: str
    source_allowlist_version: str
    source_refs: tuple[SourceRef, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "catalog_task_id": self.catalog_task_id,
            "catalog_id": self.catalog_id,
            "catalog_hash": self.catalog_hash,
            "repository_revision": self.repository_revision,
            "manifest_hash": self.manifest_hash,
            "source_allowlist_version": self.source_allowlist_version,
            "source_refs": [reference.to_dict() for reference in self.source_refs],
        }


class SourceCatalogAuthorityService:
    """Resolve only an immutable catalog already persisted by the Hub.

    Browser input identifies a catalog; it never supplies or authorizes a
    ``SRC_*``/``RUN_*`` identity. Ownership is taken from the Hub task-ingest
    event and every source identity is revalidated against the caller's tenant,
    requested scope and frozen repository/index revisions.
    """

    def __init__(self, task_repository: SourceCatalogTaskRepositoryPort | None = None) -> None:
        self._task_repository = task_repository

    def _repository(self) -> SourceCatalogTaskRepositoryPort:
        if self._task_repository is not None:
            return self._task_repository
        from agent.repository import task_repo

        return task_repo

    def resolve(
        self,
        *,
        principal: ChatSessionPrincipal,
        catalog_task_id: str,
        catalog_id: str,
        catalog_hash: str,
        repository_revision: str,
        manifest_hash: str,
        source_allowlist_version: str,
        source_scope: str,
        allowed_task_sources: Collection[str],
        allowed_task_kinds: Collection[str],
        expected_task_tenant_id: str | None = None,
        expected_task_project_id: str | None = None,
        expected_task_organization_id: str | None = None,
        organization_access_authorized: bool = False,
    ) -> ResolvedSourceCatalog:
        task_id = self._required(catalog_task_id, "source_catalog_task_id_required")
        requested_catalog_id = self._required(catalog_id, "source_catalog_id_required")
        requested_catalog_hash = self._sha256(catalog_hash, "source_catalog_hash_invalid")
        requested_revision = self._required(
            repository_revision,
            "source_catalog_repository_revision_required",
        )
        requested_manifest = self._sha256(
            manifest_hash,
            "source_catalog_manifest_hash_invalid",
        )
        requested_allowlist = self._sha256(
            source_allowlist_version,
            "source_catalog_allowlist_version_invalid",
        )
        scope = self._required(source_scope, "source_catalog_scope_required")
        expected_organization = str(
            expected_task_organization_id or ""
        ).strip()
        if expected_organization and scope != f"organization:{expected_organization}":
            raise SourceCatalogAuthorityError(
                "source_catalog_task_organization_forbidden"
            )
        sources_policy = self._normalized_policy(
            allowed_task_sources,
            "source_catalog_task_source_policy_required",
        )
        kinds_policy = self._normalized_policy(
            allowed_task_kinds,
            "source_catalog_task_kind_policy_required",
        )

        task = self._repository().get_by_id(task_id)
        if task is None:
            raise SourceCatalogAuthorityError("source_catalog_task_not_found")
        raw_task = task.model_dump() if hasattr(task, "model_dump") else dict(task)
        self._validate_task_scope(
            raw_task,
            principal=principal,
            expected_tenant_id=expected_task_tenant_id,
            expected_project_id=expected_task_project_id,
            expected_organization_id=expected_organization or None,
        )
        if str(raw_task.get("status") or "").strip().lower() != "completed":
            raise SourceCatalogAuthorityError("source_catalog_task_not_completed")
        task_kind = str(raw_task.get("task_kind") or "").strip().lower()
        if task_kind not in kinds_policy:
            raise SourceCatalogAuthorityError("source_catalog_task_kind_forbidden")
        owner, task_source = self._task_ingest_authority(raw_task)
        if expected_organization:
            # Organization catalogs are owned by the scoped Hub aggregate. The
            # publisher remains immutable audit provenance, but is not the
            # authorization principal for later Organization planning phases.
            # The Organization caller must prove current membership/grant at
            # its route/application boundary before enabling this mode.
            if organization_access_authorized is not True:
                raise SourceCatalogAuthorityError(
                    "source_catalog_organization_authority_required"
                )
        elif owner != principal.subject_id:
            raise SourceCatalogAuthorityError("source_catalog_owner_forbidden")
        if task_source not in sources_policy:
            raise SourceCatalogAuthorityError("source_catalog_task_source_forbidden")

        verification = dict(raw_task.get("verification_status") or {})
        catalog = dict(verification.get("source_catalog") or {})
        raw_sources = self._validate_catalog_projection(
            task_id=task_id,
            catalog=catalog,
            requested_catalog_id=requested_catalog_id,
            requested_catalog_hash=requested_catalog_hash,
            requested_allowlist=requested_allowlist,
            requested_manifest=requested_manifest,
        )
        references = self._resolve_source_refs(
            raw_sources,
            task_id=task_id,
            principal=principal,
            scope=scope,
            requested_revision=requested_revision,
            requested_manifest=requested_manifest,
        )
        return ResolvedSourceCatalog(
            catalog_task_id=task_id,
            catalog_id=requested_catalog_id,
            catalog_hash=requested_catalog_hash,
            repository_revision=requested_revision,
            manifest_hash=requested_manifest,
            source_allowlist_version=requested_allowlist,
            source_refs=tuple(references),
        )

    @staticmethod
    def _validate_task_scope(
        task: Mapping[str, Any],
        *,
        principal: ChatSessionPrincipal,
        expected_tenant_id: str | None,
        expected_project_id: str | None,
        expected_organization_id: str | None,
    ) -> None:
        task_tenant = str(task.get("tenant_id") or "").strip()
        task_project = str(task.get("project_id") or "").strip()
        expected_tenant = str(expected_tenant_id or "").strip()
        expected_project = str(expected_project_id or "").strip()
        task_organization = str(task.get("organization_id") or "").strip()
        expected_organization = str(
            expected_organization_id or ""
        ).strip()

        # A scoped task may never cross the authenticated tenant even when a
        # caller omitted an explicit expectation for legacy compatibility.
        if task_tenant and task_tenant != principal.tenant_id:
            raise SourceCatalogAuthorityError(
                "source_catalog_task_tenant_forbidden"
            )
        if expected_tenant and (
            expected_tenant != principal.tenant_id
            or task_tenant != expected_tenant
        ):
            raise SourceCatalogAuthorityError(
                "source_catalog_task_tenant_forbidden"
            )
        if expected_project and task_project != expected_project:
            raise SourceCatalogAuthorityError(
                "source_catalog_task_project_forbidden"
            )
        if expected_organization and task_organization != expected_organization:
            raise SourceCatalogAuthorityError(
                "source_catalog_task_organization_forbidden"
            )

    @classmethod
    def _validate_catalog_projection(
        cls,
        *,
        task_id: str,
        catalog: Mapping[str, Any],
        requested_catalog_id: str,
        requested_catalog_hash: str,
        requested_allowlist: str,
        requested_manifest: str,
    ) -> list[Mapping[str, Any]]:
        if str(catalog.get("schema") or "") != "source_catalog.v2":
            raise SourceCatalogAuthorityError("source_catalog_schema_invalid")
        if str(catalog.get("source_catalog_id") or "") != requested_catalog_id:
            raise SourceCatalogAuthorityError("source_catalog_id_mismatch")
        if str(catalog.get("source_catalog_hash") or "") != requested_catalog_hash:
            raise SourceCatalogAuthorityError("source_catalog_hash_mismatch")
        if requested_allowlist != requested_catalog_hash:
            raise SourceCatalogAuthorityError(
                "source_catalog_allowlist_version_mismatch"
            )
        if str(catalog.get("catalog_state") or "") != "current":
            raise SourceCatalogAuthorityError("source_catalog_not_current")
        rejected_count = cls._count(
            catalog.get("rejected_count"),
            "source_catalog_rejected_count_invalid",
        )
        if rejected_count != 0:
            raise SourceCatalogAuthorityError("source_catalog_contains_rejections")
        if str(catalog.get("retrieval_manifest_hash") or "") != requested_manifest:
            raise SourceCatalogAuthorityError("source_catalog_manifest_mismatch")
        if not str(catalog.get("retrieval_trace_id") or "").strip() or not str(
            catalog.get("retrieval_context_hash") or ""
        ).strip():
            raise SourceCatalogAuthorityError("source_catalog_trace_invalid")

        catalog_sources = catalog.get("sources")
        if not isinstance(catalog_sources, list):
            raise SourceCatalogAuthorityError("source_catalog_sources_invalid")
        raw_sources = list(catalog_sources)
        source_count = cls._count(
            catalog.get("source_count"),
            "source_catalog_source_count_invalid",
        )
        if not raw_sources or source_count != len(raw_sources):
            raise SourceCatalogAuthorityError("source_catalog_sources_invalid")
        for raw_source in raw_sources:
            if not isinstance(raw_source, Mapping):
                raise SourceCatalogAuthorityError("source_catalog_source_invalid")
            if any(
                raw_source.get(field) not in {None, ""}
                for field in ("content", "text", "excerpt")
            ):
                raise SourceCatalogAuthorityError("source_catalog_content_forbidden")

        canonical_catalog = cls._canonical_catalog(
            task_id=task_id,
            projection=catalog,
            sources=raw_sources,
        )
        if validate_source_catalog_payload(canonical_catalog):
            raise SourceCatalogAuthorityError("source_catalog_payload_invalid")
        recomputed_hash = calculate_source_catalog_hash(canonical_catalog)
        if recomputed_hash != requested_catalog_hash:
            raise SourceCatalogAuthorityError(
                "source_catalog_hash_integrity_mismatch"
            )
        if calculate_source_catalog_id(recomputed_hash) != requested_catalog_id:
            raise SourceCatalogAuthorityError(
                "source_catalog_id_integrity_mismatch"
            )
        return raw_sources

    @staticmethod
    def _resolve_source_refs(
        raw_sources: Collection[Mapping[str, Any]],
        *,
        task_id: str,
        principal: ChatSessionPrincipal,
        scope: str,
        requested_revision: str,
        requested_manifest: str,
    ) -> list[SourceRef]:
        references: list[SourceRef] = []
        seen: set[tuple[str, str]] = set()
        for raw_source in raw_sources:
            source = dict(raw_source)
            if str(source.get("task_id") or "") != task_id:
                raise SourceCatalogAuthorityError(
                    "source_catalog_source_task_mismatch"
                )
            if source.get("allowed_for_llm_scope") is not True:
                raise SourceCatalogAuthorityError(
                    "source_catalog_source_release_forbidden"
                )
            if str(source.get("manifest_hash") or "") != requested_manifest:
                raise SourceCatalogAuthorityError(
                    "source_catalog_source_manifest_mismatch"
                )
            try:
                reference = SourceRef.from_mapping(
                    dict(source.get("source_ref") or {})
                )
            except (TypeError, ValueError) as exc:
                raise SourceCatalogAuthorityError(
                    "source_catalog_source_ref_invalid"
                ) from exc
            if reference.tenant_id != principal.tenant_id:
                raise SourceCatalogAuthorityError(
                    "source_catalog_tenant_forbidden"
                )
            if reference.scope != scope:
                raise SourceCatalogAuthorityError("source_catalog_scope_forbidden")
            if reference.source_version != requested_revision:
                raise SourceCatalogAuthorityError(
                    "source_catalog_repository_revision_mismatch"
                )
            if any(
                str(source.get(field) or "") != expected
                for field, expected in (
                    ("source_id", reference.source_id),
                    ("source_version", reference.source_version),
                    ("tenant_id", reference.tenant_id),
                    ("scope", reference.scope),
                    ("provenance_digest", reference.provenance_digest),
                )
            ):
                raise SourceCatalogAuthorityError(
                    "source_catalog_source_ref_binding_mismatch"
                )
            key = (reference.source_id, reference.source_version)
            if key in seen:
                raise SourceCatalogAuthorityError(
                    "source_catalog_source_duplicate"
                )
            seen.add(key)
            references.append(reference)
        references.sort(key=lambda item: (item.source_id, item.source_version))
        return references

    @staticmethod
    def _canonical_catalog(
        *,
        task_id: str,
        projection: Mapping[str, Any],
        sources: list[Any],
    ) -> dict[str, Any]:
        """Restore the canonical v2 payload from the persisted task aliases."""

        return {
            "schema": projection.get("schema"),
            "catalog_id": projection.get("source_catalog_id"),
            "task_id": task_id,
            "retrieval_trace_id": projection.get("retrieval_trace_id"),
            "retrieval_context_hash": projection.get(
                "retrieval_context_hash"
            ),
            "retrieval_manifest_hash": projection.get(
                "retrieval_manifest_hash"
            ),
            "catalog_hash": projection.get("source_catalog_hash"),
            "catalog_state": projection.get("catalog_state"),
            "sources": list(sources),
            # A current catalog is accepted only when the persisted count is
            # zero, so the omitted detail projection is canonically empty.
            "rejected_candidates": [],
        }

    @staticmethod
    def _task_ingest_authority(task: Mapping[str, Any]) -> tuple[str, str]:
        matches = [
            event
            for event in list(task.get("history") or [])
            if isinstance(event, Mapping) and str(event.get("event_type") or "") == "task_ingested"
        ]
        if len(matches) != 1:
            raise SourceCatalogAuthorityError("source_catalog_task_ingest_authority_invalid")
        event = dict(matches[0])
        owner = str(event.get("actor") or "").strip()
        source = str((event.get("details") or {}).get("source") or "").strip().lower()
        if not owner or not source:
            raise SourceCatalogAuthorityError("source_catalog_task_ingest_authority_invalid")
        return owner, source

    @staticmethod
    def _required(value: Any, reason_code: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise SourceCatalogAuthorityError(reason_code)
        return normalized

    @classmethod
    def _sha256(cls, value: Any, reason_code: str) -> str:
        normalized = cls._required(value, reason_code).lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise SourceCatalogAuthorityError(reason_code)
        return normalized

    @staticmethod
    def _count(value: Any, reason_code: str) -> int:
        if isinstance(value, bool):
            raise SourceCatalogAuthorityError(reason_code)
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise SourceCatalogAuthorityError(reason_code) from exc
        if normalized < 0:
            raise SourceCatalogAuthorityError(reason_code)
        return normalized

    @staticmethod
    def _normalized_policy(values: Collection[str], reason_code: str) -> frozenset[str]:
        normalized = frozenset(str(value or "").strip().lower() for value in values if str(value or "").strip())
        if not normalized:
            raise SourceCatalogAuthorityError(reason_code)
        return normalized


_SERVICE = SourceCatalogAuthorityService()


def get_source_catalog_authority_service() -> SourceCatalogAuthorityService:
    return _SERVICE


__all__ = [
    "ResolvedSourceCatalog",
    "SourceCatalogAuthorityError",
    "SourceCatalogAuthorityService",
    "get_source_catalog_authority_service",
]
