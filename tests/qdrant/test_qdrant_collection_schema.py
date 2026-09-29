from __future__ import annotations

import math

import pytest

from worker.retrieval.qdrant_collection_schema import (
    MAX_EMBEDDING_TEXT_BYTES,
    MAX_FILE_BYTES,
    MAX_FILE_SEGMENTS,
    MAX_IMPORTANCE_SCORE,
    MAX_KIND_BYTES,
    MAX_METADATA_KEYS,
    MAX_METADATA_SEQUENCE_ITEMS,
    MAX_METADATA_STRING_BYTES,
    MAX_PARENT_ID_BYTES,
    MAX_ROLE_LABEL_BYTES,
    MAX_ROLE_LABELS,
    MAX_SOURCE_SCOPE_BYTES,
    VECTOR_PAYLOAD_INVALID,
    VECTOR_PAYLOAD_TOO_LARGE,
    CompatibilityReport,
    QdrantSchemaError,
    compatibility_diagnostics,
    deterministic_point_id,
    point_payload,
    to_client_point,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
)


def _compatibility() -> CompatibilitySpec:
    return CompatibilitySpec(
        dimensions=2,
        distance="cosine",
        provider="test-provider",
        model="test-model",
        profile="default",
        encoding="float32",
        config_hash="config-hash",
        schema_version="vector_store.v1",
        manifest_hash="manifest-hash",
    )


def _point(payload: object) -> PreparedVectorPoint:
    return PreparedVectorPoint(
        record_id="record",
        vector=(1.0, 0.0),
        scope=VectorScope("workspace", "repository", "default", "codecompass"),
        payload=payload,
        source_hash="source-hash",
    )


def _payload(
    source: dict[str, object],
    *,
    store_embedding_text: bool = False,
) -> dict[str, object]:
    return point_payload(
        _point(source),
        _compatibility(),
        store_embedding_text=store_embedding_text,
    )


def test_point_payload_preserves_normal_typed_fields_and_safe_defaults() -> None:
    payload = _payload(
        {
            "file": "src/service.py",
            "parent_id": "type:Service",
            "role_labels": (" service ", "", "api"),
            "importance_score": 3,
            "metadata": {
                "language": "python",
                "flags": [True, None, 2],
            },
        }
    )

    assert payload["kind"] == "unknown"
    assert payload["source_scope"] == "repo"
    assert payload["file"] == "src/service.py"
    assert payload["file_prefixes"] == ["src", "src/service.py"]
    assert payload["parent_id"] == "type:Service"
    assert payload["role_labels"] == ["service", "api"]
    assert payload["importance_score"] == 3.0
    assert payload["metadata"] == {
        "language": "python",
        "flags": [True, None, 2],
    }
    assert "embedding_text" not in payload


@pytest.mark.parametrize(
    "source",
    [
        {"kind": 1},
        {"file": []},
        {"parent_id": {}},
        {"source_scope": False},
        {"role_labels": "reader"},
        {"role_labels": ["reader", 1]},
        {"importance_score": "1.0"},
        {"metadata": []},
        {"metadata": {"unsupported": object()}},
        {"metadata": {"non_finite": math.inf}},
    ],
)
def test_point_payload_rejects_invalid_optional_field_types(
    source: dict[str, object],
) -> None:
    with pytest.raises(QdrantSchemaError) as exc:
        _payload(source)

    assert exc.value.reason == VECTOR_PAYLOAD_INVALID


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "x" * (MAX_KIND_BYTES + 1)},
        {"file": "x" * (MAX_FILE_BYTES + 1)},
        {"parent_id": "x" * (MAX_PARENT_ID_BYTES + 1)},
        {"source_scope": "x" * (MAX_SOURCE_SCOPE_BYTES + 1)},
        {"role_labels": ["reader"] * (MAX_ROLE_LABELS + 1)},
        {"role_labels": ["x" * (MAX_ROLE_LABEL_BYTES + 1)]},
        {"file": "/".join(["segment"] * (MAX_FILE_SEGMENTS + 1))},
        {"metadata": {f"key-{index}": index for index in range(MAX_METADATA_KEYS + 1)}},
        {"metadata": {"items": list(range(MAX_METADATA_SEQUENCE_ITEMS + 1))}},
        {"metadata": {"summary": "x" * (MAX_METADATA_STRING_BYTES + 1)}},
        {"metadata": {f"key-{index}": "x" * MAX_METADATA_STRING_BYTES for index in range(17)}},
    ],
)
def test_point_payload_rejects_oversized_optional_fields(
    source: dict[str, object],
) -> None:
    with pytest.raises(QdrantSchemaError) as exc:
        _payload(source)

    assert exc.value.reason == VECTOR_PAYLOAD_TOO_LARGE


def test_point_payload_rejects_excessive_metadata_depth() -> None:
    metadata: dict[str, object] = {"value": "ok"}
    for index in range(6):
        metadata = {f"level-{index}": metadata}

    with pytest.raises(QdrantSchemaError) as exc:
        _payload({"metadata": metadata})

    assert exc.value.reason == VECTOR_PAYLOAD_TOO_LARGE


@pytest.mark.parametrize(
    "value",
    [math.nan, math.inf, -math.inf, -0.01, MAX_IMPORTANCE_SCORE + 1],
)
def test_importance_score_must_be_finite_and_in_range(value: float) -> None:
    with pytest.raises(QdrantSchemaError) as exc:
        _payload({"importance_score": value})

    assert exc.value.reason == VECTOR_PAYLOAD_INVALID


def test_importance_and_role_label_boundaries_are_inclusive() -> None:
    payload = _payload(
        {
            "importance_score": MAX_IMPORTANCE_SCORE,
            "role_labels": ["x" * MAX_ROLE_LABEL_BYTES for _ in range(MAX_ROLE_LABELS)],
        }
    )

    assert payload["importance_score"] == MAX_IMPORTANCE_SCORE
    assert len(payload["role_labels"]) == MAX_ROLE_LABELS


def test_embedding_text_requires_opt_in_string_and_has_a_byte_limit() -> None:
    assert "embedding_text" not in _payload(
        {"embedding_text": object()},
        store_embedding_text=False,
    )

    with pytest.raises(QdrantSchemaError) as invalid:
        _payload({"embedding_text": object()}, store_embedding_text=True)
    with pytest.raises(QdrantSchemaError) as too_large:
        _payload(
            {"embedding_text": "x" * (MAX_EMBEDDING_TEXT_BYTES + 1)},
            store_embedding_text=True,
        )

    assert invalid.value.reason == VECTOR_PAYLOAD_INVALID
    assert too_large.value.reason == VECTOR_PAYLOAD_TOO_LARGE


def test_supplied_point_id_must_match_the_deterministic_scope_identity() -> None:
    point = _point({"kind": "code"})
    expected = deterministic_point_id(point.scope, point.record_id)

    implicit = to_client_point(point, _compatibility())
    explicit = to_client_point(
        PreparedVectorPoint(
            record_id=point.record_id,
            vector=point.vector,
            scope=point.scope,
            payload=point.payload,
            source_hash=point.source_hash,
            point_id=expected,
        ),
        _compatibility(),
    )

    assert implicit.point_id == expected
    assert explicit.point_id == expected


def test_mismatched_supplied_point_id_is_rejected_fail_closed() -> None:
    point = _point({"kind": "code"})
    mismatched = PreparedVectorPoint(
        record_id=point.record_id,
        vector=point.vector,
        scope=point.scope,
        payload=point.payload,
        source_hash=point.source_hash,
        point_id="00000000-0000-0000-0000-000000000000",
    )

    with pytest.raises(QdrantSchemaError) as exc:
        to_client_point(mismatched, _compatibility())

    assert exc.value.reason == "vector_point_id_mismatch"


def test_compatibility_diagnostics_are_allowlisted_bounded_and_redacted() -> None:
    diagnostics = compatibility_diagnostics(
        CompatibilityReport(
            compatible=False,
            reason="provider_changed",
            expected={
                "dimensions": 2,
                "provider": "https://user:secret@example.invalid/private",
                "model": "safe-model",
                "scope": {"workspace_id": "workspace-secret"},
                "vector": [1.0, 0.0],
            },
            found={
                "dimensions": 2,
                "provider": "safe-provider",
                "model": "x" * 300,
                "authorization": "bearer secret",
            },
        )
    )

    assert diagnostics["expected"] == {
        "dimensions": 2,
        "provider": "redacted",
        "model": "safe-model",
    }
    assert diagnostics["found"]["dimensions"] == 2
    assert diagnostics["found"]["provider"] == "safe-provider"
    assert len(diagnostics["found"]["model"].encode("utf-8")) == 256
    assert "scope" not in diagnostics["expected"]
    assert "vector" not in diagnostics["expected"]
    assert "authorization" not in diagnostics["found"]
