from __future__ import annotations

from typing import Any


def _is_embedding_record(record: dict[str, Any]) -> bool:
    provenance = dict(record.get("_provenance") or {})
    output_kind = str(provenance.get("output_kind") or "").strip().lower()
    if output_kind == "embedding":
        return True
    kind = str(record.get("kind") or "").strip().lower()
    return kind in {"embedding", "embedding_record", "vector_embedding"}


def load_codecompass_embedding_documents(
    *,
    records: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    profile_name = str(manifest.get("profile_name") or "default").strip() or "default"
    source_scope = str(manifest.get("source_scope") or "repo").strip() or "repo"
    manifest_hash = str(manifest.get("manifest_hash") or "").strip()
    loaded: list[dict[str, Any]] = []
    skipped_missing_embedding_text = 0
    skipped_non_embedding_records = 0
    candidate_count = 0

    for index, record in enumerate(list(records or []), start=1):
        if not isinstance(record, dict):
            continue
        if not _is_embedding_record(record):
            skipped_non_embedding_records += 1
            continue
        candidate_count += 1
        embedding_text = str(record.get("embedding_text") or "").strip()
        if not embedding_text:
            skipped_missing_embedding_text += 1
            continue
        provenance = dict(record.get("_provenance") or {})
        record_id = str(record.get("id") or provenance.get("record_id") or f"embedding:{index}").strip()
        loaded.append(
            {
                "schema": "codecompass_embedding_document.v1",
                "record_id": record_id,
                "kind": str(record.get("kind") or "unknown").strip().lower() or "unknown",
                "file": str(record.get("file") or record.get("path") or "").strip(),
                "parent_id": str(record.get("parent_id") or "").strip() or None,
                "role_labels": [
                    str(item).strip()
                    for item in list(record.get("role_labels") or [])
                    if str(item).strip()
                ],
                "importance_score": float(record.get("importance_score") or 0.0),
                "source_scope": str(record.get("source_scope") or source_scope).strip() or source_scope,
                "profile_name": str(record.get("profile_name") or profile_name).strip() or profile_name,
                "manifest_hash": manifest_hash,
                "embedding_text": embedding_text,
                "source_record": record,
            }
        )
    return {
        "documents": loaded,
        "diagnostics": {
            "total_records": len(list(records or [])),
            "candidate_embedding_records": candidate_count,
            "loaded_count": len(loaded),
            "skipped_missing_embedding_text": skipped_missing_embedding_text,
            "skipped_non_embedding_records": skipped_non_embedding_records,
        },
    }

