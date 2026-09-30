"""Closed Hub validation of Worker knowledge-index artifact references.

Validates each ``artifact_refs`` entry of a Worker result (roles, filenames,
sizes, graph and domain-supplement bindings) before the Hub admits any
artifact bytes.  Pure functions without I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.knowledge_index_consumption_policy import (
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
)
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
    DOMAIN_SUPPLEMENT_SCHEMA,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)

WORKER_ARTIFACT_REQUIRED_FIELDS = frozenset({"artifact_id", "sha256", "media_type"})
WORKER_ARTIFACT_OUTPUT_FIELDS = frozenset(
    {"role", "filename", "size_bytes", "knowledge_index_id", "run_id"}
)
WORKER_GRAPH_ARTIFACT_FIELDS = frozenset(
    {"artifact_schema", "graph_revision", "graph_content_hash"}
)
WORKER_DOMAIN_SUPPLEMENT_FIELDS = frozenset(
    {
        "source_revision_id",
        "source_revision_digest",
        "domain_count",
        "semantic_node_count",
        "semantic_edge_count",
        "declaration_edge_count",
    }
)
WORKER_ARTIFACT_FILENAMES = {
    "manifest": "manifest.json",
    "index": "index.jsonl",
    "details": "details.jsonl",
    "relations": "relations.jsonl",
    "graph_index": "cc_graph_index.json",
    "graph_visual_metrics": "cc_graph_index.visual_metrics.json",
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE: DOMAIN_SUPPLEMENT_FILENAME,
}
WORKER_GRAPH_ARTIFACT_SCHEMAS = {
    "graph_index": "codecompass_graph_index.v1",
    "graph_visual_metrics": "graph_visual_metrics.v1",
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE: DOMAIN_SUPPLEMENT_SCHEMA,
}
WORKER_GRAPH_ARTIFACT_MEDIA_TYPES = {
    "graph_index": "application/vnd.ananta.codecompass-graph-index+json",
    "graph_visual_metrics": (
        "application/vnd.ananta.codecompass-graph-visual-metrics+json"
    ),
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE: DOMAIN_SUPPLEMENT_MEDIA_TYPE,
}


def is_prefixed_sha256(value: str) -> bool:
    return (
        value.startswith("sha256:")
        and len(value) == 71
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def is_source_revision_id(value: str) -> bool:
    return value.startswith("srev_") and is_sha256(value[5:])


def _validate_worker_graph_artifact_reference(
    reference: Mapping[str, Any],
    *,
    role: str,
) -> None:
    if not WORKER_GRAPH_ARTIFACT_FIELDS.issubset(reference):
        raise ValueError("knowledge_index_result_graph_artifact_ref_incomplete")
    if str(reference.get("artifact_schema") or "") != WORKER_GRAPH_ARTIFACT_SCHEMAS[role]:
        raise ValueError("knowledge_index_result_graph_artifact_schema_invalid")
    if (
        role == DOMAIN_SUPPLEMENT_OUTPUT_ROLE
        and str(reference.get("media_type") or "")
        != WORKER_GRAPH_ARTIFACT_MEDIA_TYPES[role]
    ):
        raise ValueError("knowledge_index_result_graph_artifact_media_type_invalid")
    revision = str(reference.get("graph_revision") or "")
    content_hash = str(reference.get("graph_content_hash") or "")
    if not is_prefixed_sha256(revision):
        raise ValueError("knowledge_index_result_graph_revision_invalid")
    if not is_prefixed_sha256(content_hash):
        raise ValueError("knowledge_index_result_graph_content_hash_invalid")


def validate_worker_artifact_reference(reference: Any) -> None:
    if not isinstance(reference, Mapping):
        raise ValueError("knowledge_index_result_artifact_ref_invalid")
    allowed_fields = (
        WORKER_ARTIFACT_REQUIRED_FIELDS
        | WORKER_ARTIFACT_OUTPUT_FIELDS
        | WORKER_GRAPH_ARTIFACT_FIELDS
        | WORKER_DOMAIN_SUPPLEMENT_FIELDS
    )
    if (
        not WORKER_ARTIFACT_REQUIRED_FIELDS.issubset(reference)
        or set(reference) - allowed_fields
    ):
        raise ValueError("knowledge_index_result_artifact_ref_invalid")
    artifact_id = str(reference.get("artifact_id") or "").strip()
    digest = str(reference.get("sha256") or "")
    media_type = str(reference.get("media_type") or "").strip()
    if not artifact_id or not media_type:
        raise ValueError("knowledge_index_result_artifact_ref_invalid")
    if not is_sha256(digest):
        raise ValueError("knowledge_index_result_artifact_ref_digest_invalid")

    has_output_metadata = any(field in reference for field in WORKER_ARTIFACT_OUTPUT_FIELDS)
    has_graph_metadata = any(field in reference for field in WORKER_GRAPH_ARTIFACT_FIELDS)
    has_supplement_metadata = any(
        field in reference for field in WORKER_DOMAIN_SUPPLEMENT_FIELDS
    )
    if not has_output_metadata:
        if has_graph_metadata or has_supplement_metadata:
            raise ValueError("knowledge_index_result_graph_artifact_ref_incomplete")
        return
    if not WORKER_ARTIFACT_OUTPUT_FIELDS.issubset(reference):
        raise ValueError("knowledge_index_result_artifact_ref_incomplete")
    role = str(reference.get("role") or "")
    if role not in WORKER_ARTIFACT_FILENAMES:
        raise ValueError("knowledge_index_result_artifact_ref_role_invalid")
    if str(reference.get("filename") or "") != WORKER_ARTIFACT_FILENAMES[role]:
        raise ValueError("knowledge_index_result_artifact_ref_filename_invalid")
    size_bytes = reference.get("size_bytes")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
        or size_bytes > 128 * 1024 * 1024
    ):
        raise ValueError("knowledge_index_result_artifact_ref_size_invalid")
    if not str(reference.get("knowledge_index_id") or "").strip():
        raise ValueError("knowledge_index_result_artifact_ref_index_id_invalid")
    if not str(reference.get("run_id") or "").strip():
        raise ValueError("knowledge_index_result_artifact_ref_run_id_invalid")
    if role == DOMAIN_SUPPLEMENT_OUTPUT_ROLE:
        if not WORKER_DOMAIN_SUPPLEMENT_FIELDS.issubset(reference):
            raise ValueError(
                "knowledge_index_result_domain_supplement_ref_incomplete"
            )
        if size_bytes > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES:
            raise ValueError(
                "knowledge_index_result_domain_supplement_ref_size_invalid"
            )
        _validate_worker_graph_artifact_reference(reference, role=role)
        source_revision_id = str(reference.get("source_revision_id") or "")
        source_revision_digest = str(
            reference.get("source_revision_digest") or ""
        )
        if not is_source_revision_id(source_revision_id):
            raise ValueError(
                "knowledge_index_result_domain_supplement_source_revision_id_invalid"
            )
        if not is_sha256(source_revision_digest):
            raise ValueError(
                "knowledge_index_result_domain_supplement_source_revision_digest_invalid"
            )
        for field in (
            "domain_count",
            "semantic_node_count",
            "semantic_edge_count",
            "declaration_edge_count",
        ):
            count = reference.get(field)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(
                    "knowledge_index_result_domain_supplement_count_invalid"
                )
    elif role in WORKER_GRAPH_ARTIFACT_SCHEMAS:
        if has_supplement_metadata:
            raise ValueError(
                "knowledge_index_result_domain_supplement_ref_unexpected"
            )
        if size_bytes > MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES:
            raise ValueError("knowledge_index_result_graph_artifact_ref_size_invalid")
        _validate_worker_graph_artifact_reference(reference, role=role)
    elif has_graph_metadata or has_supplement_metadata:
        raise ValueError("knowledge_index_result_graph_artifact_ref_unexpected")


def validate_worker_artifact_references(
    references: list[Any],
    *,
    execution_envelope: Mapping[str, Any] | None = None,
) -> None:
    for reference in references:
        validate_worker_artifact_reference(reference)

    supplements = [
        reference
        for reference in references
        if isinstance(reference, Mapping)
        and str(reference.get("role") or "")
        == DOMAIN_SUPPLEMENT_OUTPUT_ROLE
    ]
    if not supplements:
        return
    if len(supplements) != 1:
        raise ValueError(
            "knowledge_index_result_domain_supplement_ref_duplicate"
        )
    envelope = dict(execution_envelope or {})
    if str(envelope.get("schema") or "") != KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA:
        raise ValueError(
            "knowledge_index_result_domain_supplement_requires_bound_source"
        )
    authority = envelope.get("authority_binding")
    if not isinstance(authority, Mapping):
        raise ValueError(
            "knowledge_index_result_domain_supplement_requires_bound_source"
        )
    expected_source_revision_id = str(
        authority.get("source_revision_id") or ""
    )
    expected_source_revision_digest = str(
        authority.get("source_revision_digest") or ""
    )
    if not is_source_revision_id(
        expected_source_revision_id
    ) or not is_sha256(expected_source_revision_digest):
        raise ValueError(
            "knowledge_index_result_domain_supplement_requires_bound_source"
        )

    supplement = supplements[0]
    if (
        str(supplement.get("source_revision_id") or "")
        != expected_source_revision_id
        or str(supplement.get("source_revision_digest") or "")
        != expected_source_revision_digest
    ):
        raise ValueError(
            "knowledge_index_result_domain_supplement_source_binding_mismatch"
        )
    knowledge_index_id = str(supplement.get("knowledge_index_id") or "")
    run_id = str(supplement.get("run_id") or "")
    companion_by_role: dict[str, list[Mapping[str, Any]]] = {
        role: [
            reference
            for reference in references
            if isinstance(reference, Mapping)
            and str(reference.get("role") or "") == role
            and str(reference.get("knowledge_index_id") or "")
            == knowledge_index_id
            and str(reference.get("run_id") or "") == run_id
        ]
        for role in ("graph_index", "graph_visual_metrics")
    }
    if any(len(matches) != 1 for matches in companion_by_role.values()):
        raise ValueError(
            "knowledge_index_result_domain_supplement_graph_pair_required"
        )
    if any(
        str(matches[0].get("media_type") or "")
        != WORKER_GRAPH_ARTIFACT_MEDIA_TYPES[role]
        for role, matches in companion_by_role.items()
    ):
        raise ValueError(
            "knowledge_index_result_domain_supplement_graph_pair_required"
        )
    graph_revision = str(supplement.get("graph_revision") or "")
    if any(
        str(matches[0].get("graph_revision") or "") != graph_revision
        for matches in companion_by_role.values()
    ):
        raise ValueError(
            "knowledge_index_result_domain_supplement_graph_revision_mismatch"
        )


__all__ = [
    "is_prefixed_sha256",
    "is_sha256",
    "is_source_revision_id",
    "validate_worker_artifact_reference",
    "validate_worker_artifact_references",
]
