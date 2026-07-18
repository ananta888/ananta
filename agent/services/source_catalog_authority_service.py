"""Fail-closed resolution of persisted Hub-owned source catalogs."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.chat_session_security import ChatSessionPrincipal
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
        if str(raw_task.get("status") or "").strip().lower() != "completed":
            raise SourceCatalogAuthorityError("source_catalog_task_not_completed")
        task_kind = str(raw_task.get("task_kind") or "").strip().lower()
        if task_kind not in kinds_policy:
            raise SourceCatalogAuthorityError("source_catalog_task_kind_forbidden")
        owner, task_source = self._task_ingest_authority(raw_task)
        if owner != principal.subject_id:
            raise SourceCatalogAuthorityError("source_catalog_owner_forbidden")
        if task_source not in sources_policy:
            raise SourceCatalogAuthorityError("source_catalog_task_source_forbidden")

        verification = dict(raw_task.get("verification_status") or {})
        catalog = dict(verification.get("source_catalog") or {})
        if str(catalog.get("schema") or "") != "source_catalog.v2":
            raise SourceCatalogAuthorityError("source_catalog_schema_invalid")
        if str(catalog.get("source_catalog_id") or "") != requested_catalog_id:
            raise SourceCatalogAuthorityError("source_catalog_id_mismatch")
        if str(catalog.get("source_catalog_hash") or "") != requested_catalog_hash:
            raise SourceCatalogAuthorityError("source_catalog_hash_mismatch")
        if requested_allowlist != requested_catalog_hash:
            raise SourceCatalogAuthorityError("source_catalog_allowlist_version_mismatch")
        if str(catalog.get("catalog_state") or "") != "current":
            raise SourceCatalogAuthorityError("source_catalog_not_current")
        if int(catalog.get("rejected_count") or 0) != 0:
            raise SourceCatalogAuthorityError("source_catalog_contains_rejections")
        if str(catalog.get("retrieval_manifest_hash") or "") != requested_manifest:
            raise SourceCatalogAuthorityError("source_catalog_manifest_mismatch")

        raw_sources = list(catalog.get("sources") or [])
        if not raw_sources or int(catalog.get("source_count") or 0) != len(raw_sources):
            raise SourceCatalogAuthorityError("source_catalog_sources_invalid")
        references: list[SourceRef] = []
        seen: set[tuple[str, str]] = set()
        for raw_source in raw_sources:
            if not isinstance(raw_source, Mapping):
                raise SourceCatalogAuthorityError("source_catalog_source_invalid")
            source = dict(raw_source)
            if any(source.get(field) not in {None, ""} for field in ("content", "text", "excerpt")):
                raise SourceCatalogAuthorityError("source_catalog_content_forbidden")
            if str(source.get("task_id") or "") != task_id:
                raise SourceCatalogAuthorityError("source_catalog_source_task_mismatch")
            if source.get("allowed_for_llm_scope") is not True:
                raise SourceCatalogAuthorityError("source_catalog_source_release_forbidden")
            if str(source.get("manifest_hash") or "") != requested_manifest:
                raise SourceCatalogAuthorityError("source_catalog_source_manifest_mismatch")
            try:
                reference = SourceRef.from_mapping(dict(source.get("source_ref") or {}))
            except (TypeError, ValueError) as exc:
                raise SourceCatalogAuthorityError("source_catalog_source_ref_invalid") from exc
            if reference.tenant_id != principal.tenant_id:
                raise SourceCatalogAuthorityError("source_catalog_tenant_forbidden")
            if reference.scope != scope:
                raise SourceCatalogAuthorityError("source_catalog_scope_forbidden")
            if reference.source_version != requested_revision:
                raise SourceCatalogAuthorityError("source_catalog_repository_revision_mismatch")
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
                raise SourceCatalogAuthorityError("source_catalog_source_ref_binding_mismatch")
            key = (reference.source_id, reference.source_version)
            if key in seen:
                raise SourceCatalogAuthorityError("source_catalog_source_duplicate")
            seen.add(key)
            references.append(reference)
        references.sort(key=lambda item: (item.source_id, item.source_version))
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
