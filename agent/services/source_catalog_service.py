from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "source" / "source_catalog.v1.json"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _stable_hash(payload: dict[str, Any]) -> str:
    return _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def validate_source_catalog_payload(payload: dict[str, Any]) -> list[str]:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda err: list(err.path))
    msgs = [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]
    source_ids = [str(item.get("source_id") or "") for item in list(payload.get("sources") or []) if isinstance(item, dict)]
    if len(source_ids) != len(set(source_ids)):
        msgs.append("sources: duplicate source_id values are not allowed")
    return msgs


class SourceCatalogService:
    """Build deterministic source catalogs from retrieval selected/provenance payloads."""

    _SENSITIVITY_ORDER = ["public", "internal", "internal_high", "secret", "credential", "security_sensitive"]

    def _normalize_source_type(self, entry: dict[str, Any]) -> str:
        engine = str(entry.get("engine") or entry.get("channel") or "").lower()
        kind = str(entry.get("kind") or "").lower()
        if "wiki" in kind or "wiki" in engine:
            return "wiki_chunk"
        if "artifact" in kind:
            return "artifact"
        if "repo" in kind or engine in {"repository_map"}:
            return "repo_file"
        return "rag_chunk"

    def _content_hash(self, entry: dict[str, Any]) -> str:
        raw = str(entry.get("content_hash") or entry.get("record_id") or entry.get("path") or entry.get("file") or "")
        return _sha256(raw)[:32]

    def _canonical_sort_key(self, entry: dict[str, Any]) -> tuple:
        return (
            str(entry.get("source_type") or ""),
            str(entry.get("path") or ""),
            str(entry.get("record_id") or ""),
            str(entry.get("content_hash") or ""),
        )

    def build_catalog(
        self,
        *,
        task_id: str,
        retrieval_payload: dict[str, Any],
        llm_scope: str = "local_only",
    ) -> dict[str, Any]:
        trace = dict(retrieval_payload.get("retrieval_trace") or {})
        selected = [dict(item) for item in list(retrieval_payload.get("selected") or []) if isinstance(item, dict)]
        provenance = [dict(item) for item in list(retrieval_payload.get("provenance") or []) if isinstance(item, dict)]

        merged: list[dict[str, Any]] = []
        for item in selected:
            md = dict(item.get("metadata") or {})
            merged.append(
                {
                    "source_type": self._normalize_source_type({"engine": item.get("channel"), "kind": md.get("record_kind")}),
                    "path": str(item.get("path") or md.get("file") or ""),
                    "record_id": str(item.get("record_id") or md.get("record_id") or item.get("content_hash") or ""),
                    "line_start": md.get("line_start"),
                    "line_end": md.get("line_end"),
                    "content_hash": str(item.get("content_hash") or self._content_hash(item)),
                    "manifest_hash": str(md.get("source_manifest_hash") or trace.get("manifest_hash") or ""),
                    "sensitivity": str(md.get("sensitivity") or "internal").lower(),
                }
            )
        for item in provenance:
            merged.append(
                {
                    "source_type": self._normalize_source_type(item),
                    "path": str(item.get("file") or item.get("path") or ""),
                    "record_id": str(item.get("record_id") or ""),
                    "line_start": item.get("line_start"),
                    "line_end": item.get("line_end"),
                    "content_hash": self._content_hash(item),
                    "manifest_hash": str(item.get("manifest_hash") or trace.get("manifest_hash") or ""),
                    "sensitivity": str(item.get("sensitivity") or "internal").lower(),
                }
            )

        dedup: dict[tuple, dict[str, Any]] = {}
        for row in merged:
            key = (
                row["source_type"],
                row["path"],
                row["record_id"],
                row["content_hash"],
                row["manifest_hash"],
            )
            dedup.setdefault(key, row)
        ordered = sorted(dedup.values(), key=self._canonical_sort_key)

        sources: list[dict[str, Any]] = []
        for idx, row in enumerate(ordered, start=1):
            sens = str(row.get("sensitivity") or "internal").lower()
            allowed = not (llm_scope == "external_cloud_allowed" and sens in {"internal_high", "secret", "credential", "security_sensitive"})
            sources.append(
                {
                    "source_id": f"SRC_{idx:04d}",
                    "source_type": row["source_type"],
                    "path": row["path"] or None,
                    "record_id": row["record_id"] or None,
                    "line_start": row.get("line_start"),
                    "line_end": row.get("line_end"),
                    "content_hash": row["content_hash"],
                    "manifest_hash": row["manifest_hash"] or None,
                    "sensitivity": sens if sens in self._SENSITIVITY_ORDER else "internal",
                    "allowed_for_llm_scope": bool(allowed),
                    "created_at": float(time.time()),
                    "task_id": str(task_id),
                }
            )

        base = {
            "schema": "source_catalog.v1",
            "catalog_id": f"catalog-{_sha256(str(task_id))[:16]}",
            "task_id": str(task_id),
            "retrieval_trace_id": str(trace.get("trace_id") or f"retrieval-{_sha256(str(task_id))[:12]}"),
            "retrieval_context_hash": str(trace.get("context_hash") or _sha256(str(task_id))[:16]),
            "retrieval_manifest_hash": str(trace.get("manifest_hash") or ""),
            "sources": sources,
        }
        base["catalog_hash"] = _stable_hash(
            {
                "task_id": base["task_id"],
                "retrieval_trace_id": base["retrieval_trace_id"],
                "retrieval_context_hash": base["retrieval_context_hash"],
                "retrieval_manifest_hash": base["retrieval_manifest_hash"],
                "sources": [
                    {
                        "source_id": s["source_id"],
                        "source_type": s["source_type"],
                        "path": s["path"],
                        "record_id": s["record_id"],
                        "content_hash": s["content_hash"],
                        "manifest_hash": s["manifest_hash"],
                    }
                    for s in sources
                ],
            }
        )[:32]
        errs = validate_source_catalog_payload(base)
        if errs:
            raise ValueError(f"invalid_source_catalog:{'; '.join(errs)}")
        return base


_SERVICE = SourceCatalogService()


def get_source_catalog_service() -> SourceCatalogService:
    return _SERVICE

