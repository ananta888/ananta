from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

SOURCE_TYPES: tuple[str, ...] = (
    "repo",
    "artifact",
    "task_memory",
    "wiki",
    "open_notebook",
    "curated_wiki",
)
_SOURCE_TYPE_SET = set(SOURCE_TYPES)
_NORMALIZE_SPACES = re.compile(r"\s+")
_SENSITIVITY_CLASSES = {
    "public",
    "internal_low",
    "internal_medium",
    "internal_high",
    "confidential",
    "secret",
    "credential",
    "customer_data",
    "legal",
    "security_sensitive",
}
_CLASSIFICATION_CLASSES = {
    "public",
    "internal",
    "restricted",
    "confidential",
}
_SOURCE_ORIGIN_CLASSES = {
    "repo",
    "artifact",
    "wiki",
    "external_research",
    "task_memory",
    "open_notebook",
    "curated_wiki",
}
_MISSING_SOURCE_ID_VALUES = frozenset({"", "unknown", "unverified", "none", "null", "n/a"})
_GROUNDED_SOURCE_ID_PATTERN = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")
_SOURCE_ID_KEYS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "artifact": ("artifact_id", "knowledge_index_id"),
    "wiki": ("wiki_article_id",),
    "task_memory": ("source_task_id", "memory_entry_id"),
    "open_notebook": (
        "registry_source_id",
        "open_notebook_source_id",
        "artifact_id",
    ),
    "curated_wiki": ("page_id",),
    "repo": (),
}


@runtime_checkable
class RetrievalSourceAdapter(Protocol):
    """Narrow source adapter contract for retrieval providers."""

    source_type: str

    def search(
        self,
        query: str,
        *,
        top_k: int,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """Return retrieval chunks for a query."""


@dataclass(frozen=True)
class SourceSelectionPolicy:
    enabled_source_types: frozenset[str]
    requested_source_types: tuple[str, ...]
    effective_source_types: frozenset[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": sorted(self.enabled_source_types),
            "requested": list(self.requested_source_types),
            "effective": sorted(self.effective_source_types),
        }


@dataclass(frozen=True)
class SourceIdentityVerification:
    """Catalog-bound source identity; unchecked origin IDs stay unverified."""

    source_id: str
    status: str
    reason_code: str

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "verified": self.verified,
        }


def normalize_requested_source_types(source_types: list[str] | None) -> tuple[str, ...]:
    if source_types is None:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in source_types:
        value = str(item or "").strip().lower()
        if not value:
            continue
        if value not in _SOURCE_TYPE_SET:
            raise ValueError(f"invalid_source_type:{value}")
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return tuple(normalized)


def enabled_source_types_from_settings(settings) -> frozenset[str]:
    enabled: set[str] = set()
    if bool(getattr(settings, "rag_source_repo_enabled", True)):
        enabled.add("repo")
    if bool(getattr(settings, "rag_source_artifact_enabled", True)):
        enabled.add("artifact")
    if bool(getattr(settings, "rag_source_task_memory_enabled", True)):
        enabled.add("task_memory")
    if bool(getattr(settings, "rag_source_wiki_enabled", False)):
        enabled.add("wiki")
    if bool(getattr(settings, "rag_source_open_notebook_enabled", False)):
        enabled.add("open_notebook")
    if bool(getattr(settings, "rag_source_curated_wiki_enabled", False)):
        enabled.add("curated_wiki")
    return frozenset(enabled)


def resolve_source_selection_policy(*, settings, requested_source_types: list[str] | None) -> SourceSelectionPolicy:
    enabled = enabled_source_types_from_settings(settings)
    requested = normalize_requested_source_types(requested_source_types)
    effective = frozenset(enabled.intersection(requested)) if requested else enabled
    if not effective:
        raise ValueError("no_retrieval_source_enabled")
    return SourceSelectionPolicy(
        enabled_source_types=enabled,
        requested_source_types=requested,
        effective_source_types=effective,
    )


def infer_source_type(*, engine: str, metadata: Mapping[str, Any] | None) -> str:
    payload = dict(metadata or {})
    explicit = str(payload.get("source_type") or "").strip().lower()
    if explicit in _SOURCE_TYPE_SET:
        return explicit

    source_scope = str(payload.get("source_scope") or "").strip().lower()
    if source_scope == "wiki":
        return "wiki"
    if source_scope == "open_notebook":
        return "open_notebook"
    if source_scope == "curated_wiki":
        return "curated_wiki"
    if source_scope in {"artifact", "knowledge"}:
        return "artifact"
    if source_scope in {"repo", "repo_path", "repository"}:
        return "repo"

    normalized_engine = str(engine or "").strip().lower()
    if normalized_engine == "result_memory":
        return "task_memory"
    if normalized_engine == "knowledge_index":
        if any(
            str(payload.get(key) or "").strip()
            for key in ("wiki_article_id", "article_title", "wiki_article_title", "section_title")
        ):
            return "wiki"
        return "artifact"
    return "repo"


def _source_id_candidate(value: object) -> str:
    candidate = str(value or "").strip()
    if candidate.lower() in _MISSING_SOURCE_ID_VALUES:
        return ""
    return candidate


def infer_source_id(*, source_type: str, source: str, metadata: Mapping[str, Any] | None) -> str:
    """Extract only a provided origin ID; never synthesize one from a path.

    ``source`` is retained in the signature for API compatibility and for
    provenance display by the caller.  It is deliberately not an identity
    fallback: paths, titles and ``unknown`` are not catalog-issued IDs.
    """

    del source
    payload = dict(metadata or {})
    explicit = _source_id_candidate(payload.get("source_id"))
    if explicit:
        return explicit
    normalized_type = str(source_type or "").strip().lower()
    for key in _SOURCE_ID_KEYS_BY_TYPE.get(normalized_type, ()):
        candidate = _source_id_candidate(payload.get(key))
        if candidate:
            return candidate
    return ""


def resolve_source_identity(
    *,
    source_type: str,
    source: str,
    metadata: Mapping[str, Any] | None,
    verified_source_ids: Collection[str] | None = None,
) -> SourceIdentityVerification:
    source_id = infer_source_id(
        source_type=source_type,
        source=source,
        metadata=metadata,
    )
    if not source_id:
        return SourceIdentityVerification(
            source_id="",
            status="unverified",
            reason_code="source_id_missing",
        )
    verified = {candidate for value in (verified_source_ids or ()) if (candidate := _source_id_candidate(value))}
    if source_id not in verified or _GROUNDED_SOURCE_ID_PATTERN.fullmatch(source_id) is None:
        return SourceIdentityVerification(
            source_id=source_id,
            status="unverified",
            reason_code="source_id_unverified",
        )
    return SourceIdentityVerification(
        source_id=source_id,
        status="verified",
        reason_code="source_id_verified",
    )


def source_scopes_for_types(source_types: set[str] | frozenset[str]) -> set[str]:
    scopes: set[str] = set()
    if "artifact" in source_types:
        scopes.add("artifact")
    if "wiki" in source_types:
        scopes.add("wiki")
    if "open_notebook" in source_types:
        scopes.add("open_notebook")
    if "curated_wiki" in source_types:
        scopes.add("curated_wiki")
    return scopes


def build_citation(
    *,
    source_type: str,
    source_id: str,
    source: str,
    metadata: Mapping[str, Any] | None,
    verification: SourceIdentityVerification | None = None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    identity = verification or SourceIdentityVerification(
        source_id=_source_id_candidate(source_id),
        status="unverified",
        reason_code="source_id_unverified" if _source_id_candidate(source_id) else "source_id_missing",
    )
    citation: dict[str, Any] = {
        "source_type": source_type,
        "source_id": identity.source_id if identity.verified else None,
        "verification_status": identity.status,
        "reason_code": identity.reason_code,
    }
    if source_type == "repo":
        citation["path"] = source
    elif source_type == "artifact":
        citation["artifact_id"] = payload.get("artifact_id")
        citation["knowledge_index_id"] = payload.get("knowledge_index_id")
        citation["record_kind"] = payload.get("record_kind")
    elif source_type == "task_memory":
        citation["task_id"] = payload.get("source_task_id")
        citation["memory_entry_id"] = payload.get("memory_entry_id")
    elif source_type == "wiki":
        citation["article_title"] = payload.get("article_title") or payload.get("wiki_article_title")
        citation["section_title"] = payload.get("section_title")
        citation["language"] = payload.get("language")
        citation["revision"] = payload.get("import_revision") or payload.get("revision")
    elif source_type == "open_notebook":
        citation["title"] = payload.get("source_title") or payload.get("article_title")
        citation["source_system"] = payload.get("source_system") or "open_notebook"
        citation["snapshot_id"] = payload.get("snapshot_id")
        citation["artifact_id"] = payload.get("artifact_id")
        citation["record_kind"] = payload.get("record_kind")
        citation["canonical_url"] = payload.get("canonical_url") or payload.get("file_path")
    elif source_type == "curated_wiki":
        citation["page_id"] = payload.get("page_id")
        citation["revision"] = payload.get("page_revision") or payload.get("revision")
        citation["claim_refs"] = payload.get("claim_refs")
        citation["conflict_refs"] = payload.get("conflict_refs")
        citation["coverage"] = payload.get("coverage")
        citation["authority"] = "supplement_only"
    return citation


def _build_chunk_id(
    *, source_type: str, source_id: str, engine: str, source: str, content: str, metadata: Mapping[str, Any]
) -> str:
    explicit = str(metadata.get("chunk_id") or "").strip()
    if explicit:
        return explicit
    record_id = str(metadata.get("record_id") or metadata.get("id") or "").strip()
    if record_id:
        return f"{source_type}:{record_id}"
    content_sig = _NORMALIZE_SPACES.sub(" ", str(content or "").strip().lower())[:600]
    digest = hashlib.sha1(f"{engine}|{source_id}|{source}|{content_sig}".encode("utf-8")).hexdigest()[:16]
    return f"{source_type}:{digest}"


def normalize_security_metadata(*, source_type: str, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(metadata or {})
    source_origin = str(payload.get("source_origin") or source_type or "repo").strip().lower()
    if source_origin not in _SOURCE_ORIGIN_CLASSES:
        source_origin = source_type if source_type in _SOURCE_ORIGIN_CLASSES else "repo"

    sensitivity = str(payload.get("sensitivity") or "").strip().lower()
    if sensitivity not in _SENSITIVITY_CLASSES:
        fallback = {
            "wiki": "public",
            "repo": "internal_low",
            "artifact": "internal_medium",
            "task_memory": "internal_medium",
        }
        sensitivity = fallback.get(source_type, "internal_medium")

    classification = str(payload.get("classification") or "").strip().lower()
    if classification not in _CLASSIFICATION_CLASSES:
        if sensitivity in {"public"}:
            classification = "public"
        elif sensitivity in {"internal_low", "internal_medium"}:
            classification = "internal"
        elif sensitivity in {"internal_high", "legal"}:
            classification = "restricted"
        else:
            classification = "confidential"

    tenancy = str(payload.get("tenancy") or "").strip().lower()
    if not tenancy:
        tenancy = "single_tenant"
    approval_class = str(payload.get("approval_class") or payload.get("operation_class") or "").strip().lower()
    if not approval_class:
        approval_class = "standard"

    chunk_security_tags = payload.get("chunk_security_tags")
    tags: list[str] = []
    if isinstance(chunk_security_tags, list):
        tags = [str(item).strip().lower() for item in chunk_security_tags if str(item).strip()]
    if not tags:
        tags = [classification, sensitivity, source_origin]

    return {
        "classification": classification,
        "source_origin": source_origin,
        "sensitivity": sensitivity,
        "tenancy": tenancy,
        "approval_class": approval_class,
        "chunk_security_tags": tags,
    }


def normalize_chunk_metadata(
    *,
    engine: str,
    source: str,
    content: str,
    metadata: Mapping[str, Any] | None,
    verified_source_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    source_type = infer_source_type(engine=engine, metadata=payload)
    identity = resolve_source_identity(
        source_type=source_type,
        source=source,
        metadata=payload,
        verified_source_ids=verified_source_ids,
    )
    source_id = identity.source_id
    chunk_id = _build_chunk_id(
        source_type=source_type,
        source_id=source_id,
        engine=str(engine or ""),
        source=str(source or ""),
        content=str(content or ""),
        metadata=payload,
    )
    payload["source_type"] = source_type
    payload["source_id"] = source_id or None
    payload["source_id_verification"] = identity.as_dict()
    payload["source_id_verified"] = identity.verified
    payload["chunk_id"] = chunk_id
    security_metadata = normalize_security_metadata(source_type=source_type, metadata=payload)
    payload["security_metadata"] = security_metadata
    payload["classification"] = security_metadata["classification"]
    payload["source_origin"] = security_metadata["source_origin"]
    payload["sensitivity"] = security_metadata["sensitivity"]
    payload["tenancy"] = security_metadata["tenancy"]
    payload["approval_class"] = security_metadata["approval_class"]
    payload["chunk_security_tags"] = list(security_metadata["chunk_security_tags"])
    payload["citation"] = build_citation(
        source_type=source_type,
        source_id=source_id,
        source=source,
        metadata=payload,
        verification=identity,
    )
    payload["provenance"] = {
        "engine": str(engine or ""),
        "source": str(source or ""),
        "source_scope": str(payload.get("source_scope") or "").strip() or None,
        "record_kind": str(payload.get("record_kind") or "").strip() or None,
        "source_id_verification": identity.as_dict(),
    }
    return payload
