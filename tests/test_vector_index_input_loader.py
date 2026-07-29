from __future__ import annotations

import hashlib
import json

import pytest

from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_index_input_loader import (
    BoundedVectorIndexInputLoader,
    VectorIndexInputError,
)
from worker.retrieval.vector_store_contract import VectorScope


def _scope(workspace_id: str = "workspace-a") -> VectorScope:
    return VectorScope(
        workspace_id=workspace_id,
        repository_id="repository-a",
        profile_name="default",
        domain="codecompass",
    )


def _reference(
    raw: bytes,
    *,
    scope: VectorScope | None = None,
) -> dict[str, str]:
    return VectorIndexArtifactLocator.locate(
        scope=scope or _scope(),
        content_sha256=hashlib.sha256(raw).hexdigest(),
    ).to_reference()


def _write_reference(
    root,
    raw: bytes,
    *,
    scope: VectorScope | None = None,
) -> dict[str, str]:
    reference = _reference(raw, scope=scope)
    target = root / reference["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return reference


def test_loader_reads_bounded_points_and_verifies_digest(tmp_path) -> None:
    raw = json.dumps({"points": [{"record_id": "record-a", "vector": [1.0]}]}).encode("utf-8")
    reference = _write_reference(tmp_path, raw)
    loader = BoundedVectorIndexInputLoader(
        allowed_roots=(tmp_path,),
        maximum_bytes=1024,
        maximum_points=2,
    )

    points = loader.load_points(
        reference,
        trusted_scope=_scope(),
    )

    assert points == ({"record_id": "record-a", "vector": [1.0]},)


@pytest.mark.parametrize("path", ["../outside.json", "/tmp/outside.json", "a/../../b"])
def test_loader_rejects_traversal_and_absolute_paths(tmp_path, path: str) -> None:
    loader = BoundedVectorIndexInputLoader(allowed_roots=(tmp_path,))

    with pytest.raises(
        VectorIndexInputError,
        match="vector_index_input_ref_path_invalid",
    ):
        loader.load_bytes(
            {
                **_reference(b"{}"),
                "path": path,
            },
            trusted_scope=_scope(),
        )


def test_loader_rejects_symlink_and_oversized_input(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("{}", encoding="utf-8")
    linked_reference = _reference(b"{}")
    linked_path = tmp_path / linked_reference["path"]
    linked_path.parent.mkdir(parents=True)
    linked_path.symlink_to(outside)
    large_raw = b"x" * 9
    large_reference = _write_reference(tmp_path, large_raw)
    loader = BoundedVectorIndexInputLoader(
        allowed_roots=(tmp_path,),
        maximum_bytes=8,
    )

    with pytest.raises(
        VectorIndexInputError,
        match="vector_index_input_ref_symlink_forbidden",
    ):
        loader.load_bytes(
            linked_reference,
            trusted_scope=_scope(),
        )
    with pytest.raises(
        VectorIndexInputError,
        match="vector_index_input_ref_too_large",
    ):
        loader.load_bytes(
            large_reference,
            trusted_scope=_scope(),
        )


def test_loader_rejects_digest_mismatch_and_point_limit(tmp_path) -> None:
    raw = json.dumps({"points": [{}, {}]}).encode("utf-8")
    reference = _write_reference(tmp_path, raw)
    wrong_digest_reference = VectorIndexArtifactLocator.locate(
        scope=_scope(),
        content_sha256="0" * 64,
    ).to_reference()
    wrong_digest_path = tmp_path / wrong_digest_reference["path"]
    wrong_digest_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_digest_path.write_bytes(raw)
    loader = BoundedVectorIndexInputLoader(
        allowed_roots=(tmp_path,),
        maximum_points=1,
    )

    with pytest.raises(
        VectorIndexInputError,
        match="vector_index_input_ref_digest_mismatch",
    ):
        loader.load_bytes(
            wrong_digest_reference,
            trusted_scope=_scope(),
        )
    with pytest.raises(
        VectorIndexInputError,
        match="vector_index_input_ref_points_limit_exceeded",
    ):
        loader.load_points(
            reference,
            trusted_scope=_scope(),
        )
    with pytest.raises(
        VectorIndexInputError,
        match="vector_index_input_ref_sha256_required",
    ):
        loader.load_points(
            {
                "path": reference["path"],
                "scope_fingerprint": reference["scope_fingerprint"],
            },
            trusted_scope=_scope(),
        )


def test_loader_reads_typed_document_input(tmp_path) -> None:
    raw = json.dumps(
        {
            "schema": "ananta.vector_index_documents.v1",
            "kind": "codecompass_documents",
            "documents": [
                {
                    "record_id": "record-a",
                    "embedding_text": "bounded text",
                }
            ],
        }
    ).encode("utf-8")
    reference = _write_reference(tmp_path, raw)
    loader = BoundedVectorIndexInputLoader(
        allowed_roots=(tmp_path,),
        maximum_bytes=1024,
        maximum_points=2,
    )

    loaded = loader.load_document_input(
        reference,
        trusted_scope=_scope(),
    )

    assert loaded["kind"] == "codecompass_documents"
    assert loaded["documents"][0]["record_id"] == "record-a"


@pytest.mark.parametrize(
    "payload,reason",
    [
        (
            {
                "schema": "ananta.vector_index_documents.v1",
                "kind": "codecompass_documents",
                "documents": [],
            },
            "vector_index_input_ref_documents_required",
        ),
        (
            {
                "schema": "ananta.vector_index_documents.v1",
                "kind": "codecompass_documents",
                "documents": [{}, {}],
            },
            "vector_index_input_ref_documents_limit_exceeded",
        ),
    ],
)
def test_loader_rejects_invalid_document_input(
    tmp_path,
    payload,
    reason: str,
) -> None:
    raw = json.dumps(payload).encode("utf-8")
    reference = _write_reference(tmp_path, raw)
    loader = BoundedVectorIndexInputLoader(
        allowed_roots=(tmp_path,),
        maximum_points=1,
    )

    with pytest.raises(VectorIndexInputError, match=reason):
        loader.load_document_input(
            reference,
            trusted_scope=_scope(),
        )


def test_loader_rejects_cross_scope_reference_before_resolving_file(
    tmp_path,
) -> None:
    raw = b'{"points":[{"record_id":"record-a","vector":[1.0]}]}'
    other_reference = _write_reference(
        tmp_path,
        raw,
        scope=_scope("workspace-b"),
    )
    loader = BoundedVectorIndexInputLoader(allowed_roots=(tmp_path,))

    with pytest.raises(
        VectorIndexInputError,
        match="vector_index_input_ref_scope_mismatch",
    ):
        loader.load_points(
            other_reference,
            trusted_scope=_scope("workspace-a"),
        )
