"""Closed Worker-artifact result contract admitted by the Hub.

Pure, side-effect free rules for knowledge-index Worker results: output
roles and filenames, identifier and scope normalization, reference and
budget limits and the Hub materialization binding shape.  Transport,
filesystem staging, graph admission and persistence live in sibling
collaborators of ``KnowledgeIndexWorkerArtifactService``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agent.services.knowledge_index_consumption_policy import (
    KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY,
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA,
    KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
    KNOWLEDGE_INDEX_PROJECTED_STATE,
)
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
    MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES,
)

MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_UNIT_ARTIFACT_BYTES = 384 * 1024 * 1024
MAX_RESULT_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_RESULT_UNITS = 256
MAX_GRAPH_JSON_BYTES = MAX_CODECOMPASS_GRAPH_ARTIFACT_BYTES
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
OUTPUT_FILENAMES = {
    "manifest": "manifest.json",
    "index": "index.jsonl",
    "details": "details.jsonl",
    "relations": "relations.jsonl",
    "graph_index": "cc_graph_index.json",
    "graph_visual_metrics": "cc_graph_index.visual_metrics.json",
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE: DOMAIN_SUPPLEMENT_FILENAME,
}
PRIMARY_GRAPH_ROLES = frozenset({"graph_index", "graph_visual_metrics"})
GRAPH_ROLES = PRIMARY_GRAPH_ROLES | frozenset({DOMAIN_SUPPLEMENT_OUTPUT_ROLE})
GRAPH_MEDIA_TYPES = {
    "graph_index": "application/vnd.ananta.codecompass-graph-index+json",
    "graph_visual_metrics": "application/vnd.ananta.codecompass-graph-visual-metrics+json",
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE: DOMAIN_SUPPLEMENT_MEDIA_TYPE,
}
PUBLIC_ARTIFACT_SCHEMAS = {
    "manifest": "ananta.knowledge-index.manifest.v1",
    "index": "ananta.knowledge-index.records.v1",
    "details": "ananta.knowledge-index.details.v1",
    "relations": "ananta.knowledge-index.relations.v1",
}
MAX_REFS_PER_UNIT = len(OUTPUT_FILENAMES)
MAX_RESULT_ARTIFACT_REFS = MAX_RESULT_UNITS * MAX_REFS_PER_UNIT
MATERIALIZATION_BINDING_METADATA_KEY = (
    KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY
)
MATERIALIZATION_BINDING_SCHEMA = (
    KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA
)
PENDING_PROJECTION_STATE = "pending"
JOB_ID_PATTERN = re.compile(r"^knowledge-index-[0-9a-f]{32}$")
_SOURCE_SCOPES = frozenset(
    {
        "artifact",
        "wiki",
        "repo_path",
        "registered_workspace",
        "local_directory",
        "github",
        "generic_git",
    }
)


def safe_identifier(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if not normalized or len(normalized) > 256 or any(char not in allowed for char in normalized):
        raise ValueError(f"knowledge_index_worker_{field}_invalid")
    return normalized


def source_scope_of(envelope: Mapping[str, Any]) -> str:
    job_type = str(envelope.get("job_type") or "")
    scope = str(envelope.get("source_scope") or "").strip().lower() if job_type == "source_records" else "artifact"
    if scope not in _SOURCE_SCOPES:
        raise ValueError("knowledge_index_worker_source_scope_invalid")
    return scope


def result_units(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    knowledge_index = result.get("knowledge_index")
    run = result.get("run")
    if isinstance(knowledge_index, Mapping) and isinstance(run, Mapping):
        return [{"knowledge_index": dict(knowledge_index), "run": dict(run)}]
    raw_units = result.get("results")
    if not isinstance(raw_units, list) or any(
        not isinstance(item, Mapping) for item in raw_units
    ):
        raise ValueError("knowledge_index_worker_result_units_invalid")
    units = [dict(item) for item in raw_units]
    if not units or any(
        not isinstance(unit.get("knowledge_index"), Mapping) or not isinstance(unit.get("run"), Mapping)
        for unit in units
    ):
        raise ValueError("knowledge_index_worker_result_units_invalid")
    return units


def _unit_keys(units: list[Mapping[str, Any]]) -> set[tuple[str, str]]:
    unit_keys: set[tuple[str, str]] = set()
    for unit in units:
        index_payload = unit.get("knowledge_index")
        run_payload = unit.get("run")
        if not isinstance(index_payload, Mapping) or not isinstance(
            run_payload,
            Mapping,
        ):
            raise ValueError(
                "knowledge_index_worker_result_units_invalid"
            )
        index_id = safe_identifier(
            index_payload.get("id"),
            field="index_id",
        )
        run_id = safe_identifier(
            run_payload.get("id"),
            field="run_id",
        )
        key = (index_id, run_id)
        if key in unit_keys:
            raise ValueError(
                "knowledge_index_worker_result_unit_duplicate"
            )
        unit_keys.add(key)
    return unit_keys


def _references_total_size(
    references: list[Mapping[str, Any]],
    unit_keys: set[tuple[str, str]],
) -> int:
    seen_artifact_ids: set[str] = set()
    seen_coordinates: set[tuple[str, str, str]] = set()
    refs_per_unit: dict[tuple[str, str], int] = {
        key: 0 for key in unit_keys
    }
    total_size = 0
    for reference in references:
        artifact_id = safe_identifier(
            reference.get("artifact_id"),
            field="artifact_id",
        )
        role = str(reference.get("role") or "").strip()
        if role not in OUTPUT_FILENAMES:
            raise ValueError(
                "knowledge_index_worker_artifact_role_invalid"
            )
        index_id = safe_identifier(
            reference.get("knowledge_index_id"),
            field="index_id",
        )
        run_id = safe_identifier(
            reference.get("run_id"),
            field="run_id",
        )
        unit_key = (index_id, run_id)
        if unit_key not in unit_keys:
            raise ValueError(
                "knowledge_index_worker_artifact_ref_unreferenced"
            )
        coordinates = (index_id, run_id, role)
        if (
            artifact_id in seen_artifact_ids
            or coordinates in seen_coordinates
        ):
            raise ValueError(
                "knowledge_index_worker_artifact_ref_duplicate"
            )
        seen_artifact_ids.add(artifact_id)
        seen_coordinates.add(coordinates)
        refs_per_unit[unit_key] += 1
        if refs_per_unit[unit_key] > MAX_REFS_PER_UNIT:
            raise ValueError(
                "knowledge_index_worker_artifact_ref_limit_exceeded"
            )
        raw_size = reference.get("size_bytes")
        if (
            isinstance(raw_size, bool)
            or not isinstance(raw_size, int)
            or raw_size < 0
            or raw_size > MAX_ARTIFACT_BYTES
        ):
            raise ValueError(
                "knowledge_index_worker_artifact_size_invalid"
            )
        total_size += raw_size
    return total_size


def validate_result_reference_contract(
    *,
    units: list[Mapping[str, Any]],
    references: list[Mapping[str, Any]],
    envelope: Mapping[str, Any],
    bound_v2: bool,
) -> None:
    maximum_units = 1 if bound_v2 else MAX_RESULT_UNITS
    if len(units) > maximum_units:
        raise ValueError(
            "knowledge_index_worker_result_unit_limit_exceeded"
        )
    maximum_refs = min(
        MAX_RESULT_ARTIFACT_REFS,
        len(units) * MAX_REFS_PER_UNIT,
    )
    if len(references) > maximum_refs:
        raise ValueError(
            "knowledge_index_worker_artifact_ref_limit_exceeded"
        )

    unit_keys = _unit_keys(units)
    total_size = _references_total_size(references, unit_keys)

    result_budget = MAX_RESULT_ARTIFACT_BYTES
    if bound_v2:
        resources = envelope.get("resources")
        max_output_bytes = (
            resources.get("max_output_bytes")
            if isinstance(resources, Mapping)
            else None
        )
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes < 1
            or max_output_bytes > MAX_RESULT_ARTIFACT_BYTES
        ):
            raise ValueError(
                "knowledge_index_worker_output_budget_invalid"
            )
        result_budget = max_output_bytes
    if total_size > result_budget:
        raise ValueError(
            "knowledge_index_worker_output_budget_exceeded"
        )


def validate_unit_reference_budget(
    by_role: Mapping[str, Mapping[str, Any]],
) -> None:
    total_size = 0
    for role, reference in by_role.items():
        expected_filename = OUTPUT_FILENAMES.get(role)
        if (
            expected_filename is None
            or str(reference.get("filename") or "") != expected_filename
        ):
            raise ValueError("knowledge_index_worker_artifact_role_invalid")
        raw_size = reference.get("size_bytes")
        if (
            isinstance(raw_size, bool)
            or not isinstance(raw_size, int)
            or raw_size < 0
            or raw_size > MAX_ARTIFACT_BYTES
        ):
            raise ValueError("knowledge_index_worker_artifact_size_invalid")
        if role in PRIMARY_GRAPH_ROLES and raw_size > MAX_GRAPH_JSON_BYTES:
            raise ValueError("knowledge_index_worker_graph_artifact_too_large")
        if (
            role == DOMAIN_SUPPLEMENT_OUTPUT_ROLE
            and raw_size > MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES
        ):
            raise ValueError("knowledge_index_worker_domain_supplement_too_large")
        total_size += raw_size
        if total_size > MAX_UNIT_ARTIFACT_BYTES:
            raise ValueError("knowledge_index_worker_artifact_unit_budget_exceeded")


def validate_index_source_binding(
    *,
    index_payload: Mapping[str, Any],
    source_scope: str,
    source_id: str,
    source_revision_id: str,
    source_revision_digest: str,
) -> None:
    metadata = index_payload.get("index_metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("knowledge_index_worker_domain_supplement_binding_invalid")
    if (
        len(source_revision_id) != 69
        or not source_revision_id.startswith("srev_")
        or any(
            character not in "0123456789abcdef"
            for character in source_revision_id[5:]
        )
        or len(source_revision_digest) != 64
        or any(
            character not in "0123456789abcdef"
            for character in source_revision_digest
        )
        or source_id != f"bound-source:{source_revision_id}"
        or str(metadata.get("source_scope") or "") != source_scope
        or str(metadata.get("source_id") or "") != source_id
    ):
        raise ValueError("knowledge_index_worker_domain_supplement_binding_invalid")
    for field, expected in (
        ("source_revision_id", source_revision_id),
        ("source_revision_digest", source_revision_digest),
    ):
        supplied = metadata.get(field)
        if supplied not in {None, "", expected}:
            raise ValueError(
                "knowledge_index_worker_domain_supplement_binding_invalid"
            )


def materialization_bindings(
    *,
    job_id: str,
    envelope: Mapping[str, Any],
    index_id: str,
    run_id: str,
    bound_v2: bool,
    projection_state: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_job_id = str(job_id or "").strip()
    if not JOB_ID_PATTERN.fullmatch(normalized_job_id):
        raise ValueError(
            "knowledge_index_worker_materialization_binding_invalid"
        )
    execution_job_schema = str(
        envelope.get("schema")
        or KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA
    )
    if execution_job_schema not in {
        KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA,
        KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    } or bound_v2 != (
        execution_job_schema
        == KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
    ):
        raise ValueError(
            "knowledge_index_worker_materialization_binding_invalid"
        )
    authority = envelope.get("authority_binding")
    assignment = envelope.get("assignment")
    authority_digest = str(
        authority.get("binding_digest")
        if isinstance(authority, Mapping)
        else ""
    ).strip()
    assignment_id = str(
        assignment.get("assignment_id")
        if isinstance(assignment, Mapping)
        else ""
    ).strip()
    if projection_state not in {
        PENDING_PROJECTION_STATE,
        KNOWLEDGE_INDEX_PROJECTED_STATE,
    }:
        raise ValueError(
            "knowledge_index_worker_materialization_binding_invalid"
        )
    if bound_v2 and (
        len(authority_digest) != 64
        or any(
            character not in "0123456789abcdef"
            for character in authority_digest
        )
        or not assignment_id
    ):
        raise ValueError(
            "knowledge_index_worker_materialization_binding_invalid"
        )
    common = {
        "schema": MATERIALIZATION_BINDING_SCHEMA,
        "execution_job_schema": execution_job_schema,
        "job_id": normalized_job_id,
        "authority_binding_digest": authority_digest,
        "assignment_id": assignment_id,
        "projection_state": projection_state,
    }
    return (
        {
            **common,
            "knowledge_index_id": index_id,
        },
        {
            **common,
            "knowledge_index_id": index_id,
            "run_id": run_id,
        },
    )
