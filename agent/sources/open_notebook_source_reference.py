from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from agent.sources.citation_formatter import format_citation

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "sources" / "source_reference.v1.json"

_RECORD_KINDS = {"primary_source", "note", "source_insight"}


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def validate_source_reference_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def build_source_reference(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Build a schema-valid source_reference.v1 for an OpenNotebook chunk.

    Works from normalized chunk metadata (adapter output) or importer record
    import_metadata. Missing provenance identifiers are rejected instead of
    being synthesized into apparently grounded citations.
    """
    payload = dict(metadata or {})
    record_kind = str(payload.get("record_kind") or "primary_source").strip()
    if record_kind not in _RECORD_KINDS:
        record_kind = "primary_source"

    source_id = (
        str(payload.get("registry_source_id") or "").strip()
        or str(payload.get("source_id") or "").strip()
    )
    if not source_id:
        raise ValueError("unverified_source_reference:source_id_missing")
    chunk_id = str(payload.get("chunk_id") or "").strip()
    if not chunk_id:
        raise ValueError("unverified_source_reference:chunk_id_missing")
    title = str(payload.get("source_title") or payload.get("title") or source_id).strip() or source_id

    snapshot_id = str(payload.get("snapshot_id") or payload.get("parent_source_snapshot_id") or "").strip()
    if not snapshot_id:
        raise ValueError("unverified_source_reference:snapshot_id_missing")

    canonical_url = str(payload.get("canonical_url") or "").strip()
    file_path = str(payload.get("file_path") or "").strip()
    if not canonical_url:
        if file_path:
            canonical_url = f"file:///{file_path.lstrip('/')}"
        else:
            canonical_url = f"ananta://open-notebook/{source_id}/{chunk_id}"

    citation_label = _citation_label(
        record_kind=record_kind,
        title=title,
        source_id=source_id,
        snapshot_id=snapshot_id,
        payload=payload,
    )

    reference: dict[str, Any] = {
        "schema": "source_reference.v1",
        "source_id": source_id,
        "snapshot_id": snapshot_id,
        "chunk_id": chunk_id,
        "canonical_url": canonical_url,
        "title": title,
        "license_ref": str(payload.get("license_ref") or "unknown"),
        "retrieved_at": str(payload.get("imported_at") or payload.get("retrieved_at") or _now_iso()),
        "attribution_text": citation_label,
        "extensions": {
            "source_system": str(payload.get("source_system") or "open_notebook"),
            "record_kind": record_kind,
            "citation_label": citation_label,
            "notebook_ids": list(payload.get("notebook_ids") or []),
            "open_notebook_source_id": str(payload.get("open_notebook_source_id") or "") or None,
            "artifact_id": str(payload.get("artifact_id") or "") or None,
            "content_hash": str(payload.get("content_hash") or "") or None,
            "file_path": file_path or None,
            "synthetic_snapshot": False,
        },
    }
    if record_kind == "source_insight":
        reference["extensions"]["parent_source_id"] = str(payload.get("parent_source_id") or "") or None
        reference["extensions"]["transformation_name"] = str(payload.get("transformation_name") or "") or None
        reference["extensions"]["insight_type"] = str(payload.get("insight_type") or "") or None
    if record_kind == "note":
        reference["extensions"]["note_type"] = str(payload.get("note_type") or "unknown")

    errors = validate_source_reference_payload(reference)
    if errors:
        raise ValueError(f"invalid_source_reference:{'; '.join(errors)}")
    return reference


def _citation_label(
    *,
    record_kind: str,
    title: str,
    source_id: str,
    snapshot_id: str,
    payload: Mapping[str, Any],
) -> str:
    descriptor = {
        "source_id": source_id,
        "source_type": "open_notebook",
        "display_name": title,
        "citation_source": {
            "title": title,
            "publisher": "OpenNotebook (user-managed research workspace)",
            "canonical_url": str(payload.get("canonical_url") or payload.get("file_path") or ""),
            "retrieved_at": str(payload.get("imported_at") or payload.get("retrieved_at") or ""),
            "license_ref": str(payload.get("license_ref") or "unknown"),
        },
    }
    snapshot = {"snapshot_id": snapshot_id, "content_hash": str(payload.get("content_hash") or "")}
    citation = format_citation(descriptor=descriptor, snapshot=snapshot, output_format="short")
    label = str(citation.get("rendered") or citation.get("short") or title)
    if record_kind == "note":
        return f"[note] {label}"
    if record_kind == "source_insight":
        return f"[derived insight] {label}"
    return label


def build_source_references_for_chunks(chunks: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build deduplicated references for serialized chunks (dicts with metadata)."""
    references: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in chunks:
        metadata = dict((chunk or {}).get("metadata") or {})
        if str(metadata.get("source_type") or "") != "open_notebook":
            continue
        try:
            reference = build_source_reference(metadata)
        except ValueError:
            continue
        key = f"{reference['source_id']}|{reference['snapshot_id']}|{reference['chunk_id']}"
        if key in seen:
            continue
        seen.add(key)
        references.append(reference)
    return references
