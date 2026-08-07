from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_job_service import KnowledgeIndexJobService
from ananta_contracts.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_FILENAME,
    DOMAIN_SUPPLEMENT_MEDIA_TYPE,
    DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
    DOMAIN_SUPPLEMENT_SCHEMA,
)
from ananta_contracts.codecompass_graph_limits import (
    MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES,
)
from tests.knowledge_index_execution_test_support import build_execution_job


class _Repository:
    def __init__(self, envelope: dict) -> None:
        self._task = {
            "id": envelope["job_id"],
            "worker_execution_context": {
                "knowledge_index_job": deepcopy(envelope)
            },
        }

    def get_by_id(self, task_id: str):
        return self._task if task_id == self._task["id"] else None


class _ParsedResult:
    def __init__(self, payload: dict) -> None:
        self._payload = deepcopy(payload)

    def to_wire(self) -> dict:
        return deepcopy(self._payload)


class _BindingService:
    def validate_result(self, *, payload, **_options):
        return SimpleNamespace(), _ParsedResult(payload)


def _references() -> list[dict]:
    graph_revision = "sha256:" + "1" * 64
    common = {
        "sha256": "2" * 64,
        "size_bytes": 1024,
        "knowledge_index_id": "idx-1",
        "run_id": "run-1",
        "graph_revision": graph_revision,
        "graph_content_hash": "sha256:" + "3" * 64,
    }
    return [
        {
            **common,
            "artifact_id": "artifact-graph",
            "media_type": (
                "application/vnd.ananta.codecompass-graph-index+json"
            ),
            "role": "graph_index",
            "filename": "cc_graph_index.json",
            "artifact_schema": "codecompass_graph_index.v1",
        },
        {
            **common,
            "artifact_id": "artifact-metrics",
            "media_type": (
                "application/vnd.ananta.codecompass-graph-visual-metrics+json"
            ),
            "role": "graph_visual_metrics",
            "filename": "cc_graph_index.visual_metrics.json",
            "artifact_schema": "graph_visual_metrics.v1",
        },
        {
            **common,
            "artifact_id": "artifact-domains",
            "media_type": DOMAIN_SUPPLEMENT_MEDIA_TYPE,
            "role": DOMAIN_SUPPLEMENT_OUTPUT_ROLE,
            "filename": DOMAIN_SUPPLEMENT_FILENAME,
            "artifact_schema": DOMAIN_SUPPLEMENT_SCHEMA,
            "source_revision_id": "srev_" + "a" * 64,
            "source_revision_digest": "b" * 64,
            "domain_count": 3,
            "semantic_node_count": 100,
            "semantic_edge_count": 75,
            "declaration_edge_count": 25,
        },
    ]


def _validate(references: list[dict], *, envelope: dict | None = None) -> dict:
    bound_envelope = envelope or build_execution_job().to_wire()
    service = KnowledgeIndexJobService(
        task_repository=_Repository(bound_envelope),
        execution_binding_service=_BindingService(),
    )
    payload = {
        "schema": "ananta.knowledge_index_execution_result.v2",
        "job_id": bound_envelope["job_id"],
        "artifact_refs": references,
    }
    return service.validate_worker_result(
        job_id=bound_envelope["job_id"],
        result=payload,
        authenticated_worker_id="worker-index-01",
    )


def test_accepts_strict_revision_bound_domain_supplement_reference() -> None:
    result = _validate(_references())

    supplement = result["artifact_refs"][2]
    assert supplement["role"] == DOMAIN_SUPPLEMENT_OUTPUT_ROLE
    assert supplement["source_revision_id"] == "srev_" + "a" * 64
    assert supplement["semantic_node_count"] == 100


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("filename", "domains.sqlite3", "artifact_ref_filename_invalid"),
        ("media_type", "application/octet-stream", "artifact_media_type_invalid"),
        ("artifact_schema", "codecompass_graph_domain_supplement.v2", "artifact_schema_invalid"),
        ("size_bytes", MAX_CODECOMPASS_DOMAIN_SUPPLEMENT_BYTES + 1, "artifact_ref_size_invalid"),
        ("sha256", "A" * 64, "artifact_ref_digest_invalid"),
        ("graph_content_hash", "3" * 64, "graph_content_hash_invalid"),
        ("source_revision_id", "srev_invalid", "source_revision_id_invalid"),
        ("source_revision_digest", "sha256:" + "b" * 64, "source_revision_digest_invalid"),
        ("semantic_edge_count", -1, "supplement_count_invalid"),
        ("domain_count", True, "supplement_count_invalid"),
    ],
)
def test_rejects_invalid_domain_supplement_fields(
    field: str,
    value: object,
    reason: str,
) -> None:
    references = _references()
    references[2][field] = value

    with pytest.raises(ValueError, match=reason):
        _validate(references)


@pytest.mark.parametrize("failure", ["missing_pair", "revision", "source"])
def test_rejects_unbound_or_unpaired_domain_supplement(failure: str) -> None:
    references = _references()
    if failure == "missing_pair":
        references.pop(1)
        reason = "supplement_graph_pair_required"
    elif failure == "revision":
        references[2]["graph_revision"] = "sha256:" + "4" * 64
        reason = "supplement_graph_revision_mismatch"
    else:
        references[2]["source_revision_digest"] = "c" * 64
        reason = "supplement_source_binding_mismatch"

    with pytest.raises(ValueError, match=reason):
        _validate(references)


def test_rejects_domain_supplement_without_v2_authority_binding() -> None:
    envelope = build_execution_job().to_wire()
    envelope.pop("authority_binding")

    with pytest.raises(ValueError, match="supplement_requires_bound_source"):
        _validate(_references(), envelope=envelope)
