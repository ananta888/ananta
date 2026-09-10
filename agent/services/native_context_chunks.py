"""Normalize existing persisted retrieval chunks without interpreting authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ananta_contracts.context_access_policy import Sensitivity, SourceType

_ENGINE_SOURCE_TYPES = {
    "codecompass_fts": SourceType.codecompass_code,
    "codecompass_graph": SourceType.codecompass_graph,
    "repository_map": SourceType.local_file,
}
_SOURCE_TYPES = {"repo": SourceType.local_file, "wiki": SourceType.docs}


def native_context_chunk_fields(raw: Any) -> tuple[str, str, SourceType, Sensitivity | None]:
    if not isinstance(raw, Mapping):
        raise ValueError("native_context_chunk_invalid")
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("native_context_chunk_invalid")
    if any(values.get(key) for values in (raw, metadata) for key in ("approval_override_id", "approval_scope")):
        raise ValueError("native_context_chunk_approval_unsupported")
    content = _one_value(raw.get("content"), raw.get("text"))
    source_ref = _one_value(raw.get("source_ref"), raw.get("source")) or metadata.get("file")
    kind = _one_value(raw.get("source_type"), metadata.get("source_type"))
    sensitivity = _one_value(raw.get("sensitivity"), metadata.get("sensitivity"))
    try:
        source_type = _SOURCE_TYPES.get(kind) or (SourceType(kind) if kind is not None else None)
        if source_type is None:
            source_type = _ENGINE_SOURCE_TYPES.get(raw.get("engine"))
        if source_type is None:
            raise ValueError("source_type_required")
        classified = Sensitivity(sensitivity) if sensitivity is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError("native_context_chunk_classification_invalid") from exc
    return content, source_ref, source_type, classified


def _one_value(primary: Any, fallback: Any) -> Any:
    if primary is not None and fallback is not None and primary != fallback:
        raise ValueError("native_context_chunk_field_conflict")
    return primary if primary is not None else fallback
