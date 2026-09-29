from __future__ import annotations

import hashlib

import pytest

from agent.services.vector_index_input_artifact_service import (
    FilesystemVectorIndexInputPublisher,
    VectorIndexInputPublishError,
    build_vector_index_input_publisher,
)
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_store_contract import VectorScope


def _scope() -> VectorScope:
    return VectorScope(
        workspace_id="workspace-a",
        repository_id="repository-a",
        profile_name="default",
        domain="codecompass",
    )


def test_publisher_writes_content_addressed_atomic_input(tmp_path) -> None:
    root = tmp_path / "published"
    content = b'{"documents":[]}'
    digest = hashlib.sha256(content).hexdigest()
    publisher = FilesystemVectorIndexInputPublisher(publish_root=root)

    first = publisher.publish(
        scope=_scope(),
        content=content,
        content_sha256=digest,
    )
    second = publisher.publish(
        scope=_scope(),
        content=content,
        content_sha256=digest,
    )

    assert first == second
    target = root / str(first["path"])
    assert target.read_bytes() == content
    assert first["sha256"] == digest
    assert (
        first
        == VectorIndexArtifactLocator.locate(
            scope=_scope(),
            content_sha256=digest,
        ).to_reference()
    )
    assert first["scope_fingerprint"] == _scope().fingerprint()
    assert "workspace-a" not in str(first["path"])
    assert not list(root.rglob("*.tmp"))


def test_publisher_isolates_same_digest_by_full_scope_fingerprint(
    tmp_path,
) -> None:
    root = tmp_path / "published"
    content = b'{"documents":[]}'
    digest = hashlib.sha256(content).hexdigest()
    publisher = FilesystemVectorIndexInputPublisher(publish_root=root)
    other_scope = VectorScope(
        workspace_id="workspace-b",
        repository_id="repository-a",
        profile_name="default",
        domain="codecompass",
    )

    first = publisher.publish(
        scope=_scope(),
        content=content,
        content_sha256=digest,
    )
    second = publisher.publish(
        scope=other_scope,
        content=content,
        content_sha256=digest,
    )

    assert first["path"] != second["path"]
    assert first["scope_fingerprint"] != second["scope_fingerprint"]
    assert (root / str(first["path"])).is_file()
    assert (root / str(second["path"])).is_file()


def test_publisher_rejects_digest_mismatch_and_oversized_content(tmp_path) -> None:
    publisher = FilesystemVectorIndexInputPublisher(
        publish_root=tmp_path / "published",
        maximum_bytes=4,
    )

    with pytest.raises(
        VectorIndexInputPublishError,
        match="vector_index_input_publish_digest_mismatch",
    ):
        publisher.publish(
            scope=_scope(),
            content=b"1234",
            content_sha256="0" * 64,
        )
    with pytest.raises(
        VectorIndexInputPublishError,
        match="vector_index_input_publish_content_too_large",
    ):
        publisher.publish(
            scope=_scope(),
            content=b"12345",
            content_sha256=hashlib.sha256(b"12345").hexdigest(),
        )


def test_publisher_bounds_existing_content_before_digesting(tmp_path) -> None:
    root = tmp_path / "published"
    publisher = FilesystemVectorIndexInputPublisher(
        publish_root=root,
        maximum_bytes=4,
    )
    content = b"1234"
    digest = hashlib.sha256(content).hexdigest()
    published = publisher.publish(
        scope=_scope(),
        content=content,
        content_sha256=digest,
    )
    (root / str(published["path"])).write_bytes(b"oversized")

    with pytest.raises(
        VectorIndexInputPublishError,
        match="vector_index_input_publish_existing_too_large",
    ):
        publisher.publish(
            scope=_scope(),
            content=content,
            content_sha256=digest,
        )


def test_publisher_requires_explicit_absolute_root(tmp_path) -> None:
    assert build_vector_index_input_publisher(environ={}) is None
    with pytest.raises(
        ValueError,
        match="vector_index_input_publish_root_must_be_absolute",
    ):
        FilesystemVectorIndexInputPublisher(publish_root="relative")

    built = build_vector_index_input_publisher(
        environ={"ANANTA_VECTOR_INDEX_INPUT_PUBLISH_ROOT": str(tmp_path / "published")}
    )
    assert isinstance(built, FilesystemVectorIndexInputPublisher)
