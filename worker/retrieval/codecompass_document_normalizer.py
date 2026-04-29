from __future__ import annotations

import hashlib
import json
from typing import Any


def _join_scalars(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_join_scalars(item) for item in value if _join_scalars(item))
    if isinstance(value, dict):
        parts: list[str] = []
        for key in sorted(value):
            joined = _join_scalars(value[key])
            if joined:
                parts.append(joined)
        return " ".join(parts)
    return ""


def _deterministic_hash(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _text_fields(record: dict[str, Any]) -> dict[str, str]:
    return {
        "symbol_text": _join_scalars(record.get("symbol") or record.get("symbols") or record.get("name") or record.get("title")),
        "path_text": _join_scalars(record.get("path") or record.get("file")),
        "summary_text": _join_scalars(record.get("summary")),
        "content_text": _join_scalars(record.get("content") or record.get("text")),
        "relation_text": _join_scalars(record.get("relation") or record.get("relations")),
        "focus_text": _join_scalars(
            record.get("focus_terms")
            or record.get("retrieval_focus")
            or record.get("member_names")
            or record.get("role_labels")
            or record.get("embedding_text")
        ),
    }


def normalize_codecompass_records(*, records: list[dict[str, Any]], manifest_hash: str) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(list(records or []), start=1):
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("id") or record.get("_provenance", {}).get("record_id") or f"record:{index}").strip()
        text_fields = _text_fields(record)
        doc = {
            "schema": "codecompass_retrieval_document.v1",
            "document_hash": "",
            "record_id": record_id,
            "kind": str(record.get("kind") or record.get("type") or "unknown").strip().lower() or "unknown",
            "file": str(record.get("file") or record.get("path") or "").strip(),
            "parent_id": str(record.get("parent_id") or "").strip() or None,
            "role_labels": [str(item).strip() for item in list(record.get("role_labels") or []) if str(item).strip()],
            "importance_score": float(record.get("importance_score") or 0.0),
            "generated_code": bool(record.get("generated_code", False)),
            "manifest_hash": str(manifest_hash or "").strip(),
            "text_fields": text_fields,
            "source_record": record,
        }
        doc["document_hash"] = _deterministic_hash(
            {
                "record_id": doc["record_id"],
                "kind": doc["kind"],
                "file": doc["file"],
                "parent_id": doc["parent_id"],
                "role_labels": doc["role_labels"],
                "importance_score": doc["importance_score"],
                "generated_code": doc["generated_code"],
                "manifest_hash": doc["manifest_hash"],
                "text_fields": doc["text_fields"],
            }
        )
        normalized.append(doc)
    return normalized

