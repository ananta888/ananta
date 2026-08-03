from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ananta_contracts.retrieval import SourceRef

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "source"
_SCHEMA_PATHS = {
    "source_catalog.v1": _SCHEMA_DIR / "source_catalog.v1.json",
    "source_catalog.v2": _SCHEMA_DIR / "source_catalog.v2.json",
}
_SOURCE_ID_PATTERN = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _stable_hash(payload: Any) -> str:
    return _sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def validate_source_catalog_payload(payload: dict[str, Any]) -> list[str]:
    schema_name = str(payload.get("schema") or "")
    schema_path = _SCHEMA_PATHS.get(schema_name)
    if schema_path is None:
        return [f"$: unsupported source catalog schema: {schema_name or '<missing>'}"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda err: list(err.path))
    msgs = [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]
    source_keys = [
        (
            str(item.get("source_id") or ""),
            str(item.get("source_version") or "") if schema_name == "source_catalog.v2" else "",
        )
        for item in list(payload.get("sources") or [])
        if isinstance(item, dict)
    ]
    if len(source_keys) != len(set(source_keys)):
        label = "source identity" if schema_name == "source_catalog.v2" else "source_id"
        msgs.append(f"sources: duplicate {label} values are not allowed")
    return msgs


def source_catalog_integrity_projection(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the exact v2 fields covered by the catalog content hash.

    Persisted task projections use aliases for the catalog ID/hash and omit
    zero rejected-candidate details.  Keeping the integrity projection in one
    public helper lets both the producer and the read authority apply the same
    canonical algorithm without reaching into a private implementation detail.
    """

    return {
        "task_id": payload.get("task_id"),
        "retrieval_trace_id": payload.get("retrieval_trace_id"),
        "retrieval_context_hash": payload.get("retrieval_context_hash"),
        "retrieval_manifest_hash": payload.get("retrieval_manifest_hash"),
        "sources": list(payload.get("sources") or []),
        "rejected_candidates": list(payload.get("rejected_candidates") or []),
    }


def calculate_source_catalog_hash(payload: Mapping[str, Any]) -> str:
    """Recompute the deterministic SHA-256 integrity binding for a v2 catalog."""

    return _stable_hash(source_catalog_integrity_projection(payload))


def calculate_source_catalog_id(catalog_hash: str) -> str:
    """Derive the deterministic catalog identifier from a validated hash."""

    return f"catalog-{str(catalog_hash or '').strip().lower()[:16]}"


class SourceCatalogService:
    """Build a deterministic, fail-closed catalog from authoritative references.

    Source IDs and versions are accepted only when the retrieval provider supplied
    them.  Paths, list positions, task IDs and record IDs are never identity
    fallbacks.  Rejected candidates remain observable through content-free audit
    stubs, while only verified ``SourceRef`` values enter the citation catalog.
    """

    _SENSITIVITY_ORDER = {
        "public",
        "internal",
        "internal_high",
        "secret",
        "credential",
        "security_sensitive",
    }

    def _normalize_source_type(self, entry: Mapping[str, Any]) -> str:
        engine = str(entry.get("engine") or entry.get("channel") or "").lower()
        kind = str(entry.get("kind") or entry.get("record_kind") or "").lower()
        if "wiki" in kind or "wiki" in engine:
            return "wiki_chunk"
        if "artifact" in kind:
            return "artifact"
        if "test" in kind:
            return "test_result"
        if "repo" in kind or engine in {"repository_map", "codecompass_fts", "codecompass_vector"}:
            return "repo_file"
        return "rag_chunk"

    @staticmethod
    def _first(*values: object) -> str:
        for value in values:
            normalized = str(value or "").strip()
            if normalized:
                return normalized
        return ""

    def _candidate(self, item: Mapping[str, Any], *, trace: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(item)
        metadata = dict(raw.get("metadata") or {})
        provenance_raw = raw.get("provenance") or metadata.get("source_provenance") or metadata.get("provenance")
        provenance = dict(provenance_raw) if isinstance(provenance_raw, Mapping) else {}
        provenance_digest = self._first(
            raw.get("provenance_digest"),
            metadata.get("provenance_digest"),
            provenance.get("provenance_digest"),
        )
        if not provenance_digest and provenance:
            provenance_digest = _stable_hash(provenance)
        return {
            "source_id": self._first(
                raw.get("source_id"),
                metadata.get("source_id"),
                metadata.get("registry_source_id"),
            ),
            "source_version": self._first(
                raw.get("source_version"),
                metadata.get("source_version"),
                provenance.get("source_version"),
            ),
            "tenant_id": self._first(
                raw.get("tenant_id"),
                metadata.get("tenant_id"),
                provenance.get("tenant_id"),
                trace.get("tenant_id"),
            ),
            "scope": self._first(
                raw.get("scope"),
                metadata.get("scope"),
                metadata.get("source_scope"),
                provenance.get("scope"),
                trace.get("scope"),
            ),
            "provenance_digest": provenance_digest.lower(),
            "source_type": self._normalize_source_type(
                {
                    "engine": raw.get("engine") or raw.get("channel"),
                    "kind": raw.get("kind") or metadata.get("record_kind"),
                }
            ),
            "path": self._first(raw.get("path"), raw.get("file"), metadata.get("path"), metadata.get("file")),
            "record_id": self._first(raw.get("record_id"), metadata.get("record_id")),
            "line_start": raw.get("line_start", metadata.get("line_start")),
            "line_end": raw.get("line_end", metadata.get("line_end")),
            "content_hash": self._first(raw.get("content_hash"), metadata.get("content_hash")),
            "manifest_hash": self._first(
                raw.get("manifest_hash"),
                metadata.get("source_manifest_hash"),
                provenance.get("manifest_hash"),
                trace.get("manifest_hash"),
            ),
            "sensitivity": self._first(raw.get("sensitivity"), metadata.get("sensitivity"), "internal").lower(),
        }

    @staticmethod
    def _candidate_digest(row: Mapping[str, Any]) -> str:
        return _stable_hash(
            {
                "source_id": row.get("source_id"),
                "source_version": row.get("source_version"),
                "tenant_id": row.get("tenant_id"),
                "scope": row.get("scope"),
                "provenance_digest": row.get("provenance_digest"),
                "path": row.get("path"),
                "record_id": row.get("record_id"),
                "content_hash": row.get("content_hash"),
                "manifest_hash": row.get("manifest_hash"),
            }
        )

    @staticmethod
    def _rejection_reason(row: Mapping[str, Any]) -> str | None:
        source_id = str(row.get("source_id") or "")
        if not source_id:
            return "source_id_missing"
        if _SOURCE_ID_PATTERN.fullmatch(source_id) is None:
            return "source_id_invalid"
        if not str(row.get("source_version") or ""):
            return "source_version_missing"
        if not str(row.get("tenant_id") or ""):
            return "source_tenant_missing"
        if not str(row.get("scope") or ""):
            return "source_scope_missing"
        if not str(row.get("provenance_digest") or ""):
            return "source_provenance_missing"
        if not str(row.get("content_hash") or ""):
            return "source_content_hash_missing"
        return None

    def build_catalog(
        self,
        *,
        task_id: str,
        retrieval_payload: dict[str, Any],
        llm_scope: str = "local_only",
    ) -> dict[str, Any]:
        trace = dict(retrieval_payload.get("retrieval_trace") or {})
        raw_items = [
            *[dict(item) for item in list(retrieval_payload.get("selected") or []) if isinstance(item, dict)],
            *[dict(item) for item in list(retrieval_payload.get("provenance") or []) if isinstance(item, dict)],
        ]
        rows = sorted(
            (self._candidate(item, trace=trace) for item in raw_items),
            key=self._candidate_digest,
        )

        sources: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        seen_refs: set[tuple[str, str, str, str]] = set()
        for row in rows:
            reason = self._rejection_reason(row)
            source_ref: SourceRef | None = None
            if reason is None:
                try:
                    source_ref = SourceRef(
                        source_id=str(row["source_id"]),
                        source_version=str(row["source_version"]),
                        tenant_id=str(row["tenant_id"]),
                        scope=str(row["scope"]),
                        provenance_digest=str(row["provenance_digest"]),
                    )
                except ValueError:
                    reason = "source_ref_invalid"
            if reason is not None or source_ref is None:
                rejected.append(
                    {
                        "candidate_digest": self._candidate_digest(row),
                        "reason_code": reason or "source_ref_invalid",
                    }
                )
                continue
            ref_key = (
                source_ref.source_id,
                source_ref.source_version,
                source_ref.tenant_id,
                source_ref.scope,
            )
            if ref_key in seen_refs:
                rejected.append(
                    {
                        "candidate_digest": self._candidate_digest(row),
                        "reason_code": "source_duplicate",
                    }
                )
                continue
            seen_refs.add(ref_key)
            sensitivity = str(row.get("sensitivity") or "internal")
            if sensitivity not in self._SENSITIVITY_ORDER:
                sensitivity = "internal"
            external_denied = (
                llm_scope == "external_cloud_allowed"
                and sensitivity in {"internal_high", "secret", "credential", "security_sensitive"}
            )
            sources.append(
                {
                    "source_ref": source_ref.to_dict(),
                    "source_id": source_ref.source_id,
                    "source_version": source_ref.source_version,
                    "tenant_id": source_ref.tenant_id,
                    "scope": source_ref.scope,
                    "provenance_digest": source_ref.provenance_digest,
                    "source_type": row["source_type"],
                    "path": row["path"] or None,
                    "record_id": row["record_id"] or None,
                    "line_start": row.get("line_start"),
                    "line_end": row.get("line_end"),
                    "content_hash": row["content_hash"],
                    "manifest_hash": row["manifest_hash"] or None,
                    "sensitivity": sensitivity,
                    "allowed_for_llm_scope": not external_denied,
                    "task_id": str(task_id),
                }
            )

        sources.sort(
            key=lambda item: (
                str(item["source_id"]),
                str(item["source_version"]),
                str(item["tenant_id"]),
                str(item["scope"]),
            )
        )
        rejected.sort(key=lambda item: (item["candidate_digest"], item["reason_code"]))
        trace_id = self._first(trace.get("trace_id")) or None
        context_hash = self._first(trace.get("context_hash")) or None
        manifest_hash = self._first(trace.get("manifest_hash")) or None
        projection = {
            "task_id": str(task_id),
            "retrieval_trace_id": trace_id,
            "retrieval_context_hash": context_hash,
            "retrieval_manifest_hash": manifest_hash,
            "sources": sources,
            "rejected_candidates": rejected,
        }
        catalog_hash = calculate_source_catalog_hash(projection)
        base = {
            "schema": "source_catalog.v2",
            "catalog_id": calculate_source_catalog_id(catalog_hash),
            **projection,
            "catalog_hash": catalog_hash,
            "catalog_state": (
                "current"
                if sources and not rejected and trace_id and context_hash and manifest_hash
                else "degraded"
            ),
        }
        errors = validate_source_catalog_payload(base)
        if errors:
            raise ValueError(f"invalid_source_catalog:{'; '.join(errors)}")
        return base

    def migrate_v1_catalog(
        self,
        catalog: Mapping[str, Any],
        *,
        tenant_id: str,
        scope: str,
    ) -> dict[str, Any]:
        """Adapt supplied v1 identities without manufacturing new source IDs."""

        if str(catalog.get("schema") or "") != "source_catalog.v1":
            raise ValueError("source_catalog_v1_required")
        selected: list[dict[str, Any]] = []
        for source in list(catalog.get("sources") or []):
            if not isinstance(source, Mapping):
                continue
            manifest_hash = self._first(source.get("manifest_hash"), catalog.get("retrieval_manifest_hash"))
            selected.append(
                {
                    **dict(source),
                    "source_version": manifest_hash,
                    "tenant_id": tenant_id,
                    "scope": scope,
                    "provenance": {
                        "migration": "source_catalog.v1",
                        "catalog_hash": str(catalog.get("catalog_hash") or ""),
                        "source_id": str(source.get("source_id") or ""),
                        "source_version": manifest_hash,
                    },
                }
            )
        return self.build_catalog(
            task_id=str(catalog.get("task_id") or ""),
            retrieval_payload={
                "selected": selected,
                "retrieval_trace": {
                    "trace_id": catalog.get("retrieval_trace_id"),
                    "context_hash": catalog.get("retrieval_context_hash"),
                    "manifest_hash": catalog.get("retrieval_manifest_hash"),
                    "tenant_id": tenant_id,
                    "scope": scope,
                },
            },
        )


_SERVICE = SourceCatalogService()


def get_source_catalog_service() -> SourceCatalogService:
    return _SERVICE
