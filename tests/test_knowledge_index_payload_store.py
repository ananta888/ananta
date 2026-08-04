from __future__ import annotations

import errno
import hashlib

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.services.artifact_store import ArtifactStore
from agent.services.knowledge_index_payload_store import (
    ContentAddressedKnowledgeIndexPayloadStore,
)


def _payload_store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'artifacts.sqlite3'}")
    SQLModel.metadata.create_all(
        engine,
        tables=[ArtifactDB.__table__, ArtifactVersionDB.__table__],
    )
    artifacts = ArtifactStore(tmp_path / "artifacts")
    return engine, artifacts, ContentAddressedKnowledgeIndexPayloadStore(
        engine=engine,
        artifact_store=artifacts,
    )


def test_payload_reference_is_content_addressed_across_store_restart(
    tmp_path,
) -> None:
    engine, artifacts, first_store = _payload_store(tmp_path)
    content = b'{"records":[],"source_id":"example"}'
    fingerprint = hashlib.sha256(content).hexdigest()

    first = first_store.store_payload(
        content=content,
        fingerprint=fingerprint,
        created_by="owner-alice",
    )
    restarted_store = ContentAddressedKnowledgeIndexPayloadStore(
        engine=engine,
        artifact_store=ArtifactStore(artifacts.base_dir),
    )
    replay = restarted_store.store_payload(
        content=content,
        fingerprint=fingerprint,
        created_by="owner-alice",
    )

    assert replay == first
    assert first["artifact_id"] == f"knowledge-index-payload-{fingerprint}"
    assert artifacts.load_immutable_bytes(
        artifact_id=str(first["artifact_id"]),
        version_number=1,
        filename="payload.json",
        expected_sha256=fingerprint,
        expected_size=len(content),
    ) == content
    with Session(engine) as session:
        assert len(session.exec(select(ArtifactDB)).all()) == 1
        assert len(session.exec(select(ArtifactVersionDB)).all()) == 1


def test_failed_atomic_publish_cleans_temp_and_writes_no_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    engine, artifacts, payload_store = _payload_store(tmp_path)
    content = b'{"records":[{"id":"one"}]}'
    fingerprint = hashlib.sha256(content).hexdigest()

    def fail_publish(*_args, **_kwargs):
        raise OSError(errno.EIO, "simulated publish interruption")

    monkeypatch.setattr(artifacts, "_rename_noreplace", fail_publish)
    with pytest.raises(ValueError, match="immutable_artifact_unavailable"):
        payload_store.store_payload(
            content=content,
            fingerprint=fingerprint,
            created_by="owner-alice",
        )

    artifact_directory = (
        artifacts.base_dir / f"knowledge-index-payload-{fingerprint}"
    )
    assert list(artifact_directory.iterdir()) == []
    with Session(engine) as session:
        assert session.exec(select(ArtifactDB)).all() == []
        assert session.exec(select(ArtifactVersionDB)).all() == []

    monkeypatch.undo()
    stored = payload_store.store_payload(
        content=content,
        fingerprint=fingerprint,
        created_by="owner-alice",
    )
    assert artifacts.load_immutable_bytes(
        artifact_id=str(stored["artifact_id"]),
        version_number=1,
        filename="payload.json",
        expected_sha256=fingerprint,
        expected_size=len(content),
    ) == content


def test_restart_repairs_metadata_after_publish_completed_before_commit(
    tmp_path,
) -> None:
    engine, artifacts, payload_store = _payload_store(tmp_path)
    content = b'{"records":[{"id":"published"}]}'
    fingerprint = hashlib.sha256(content).hexdigest()
    reference = payload_store.prepare_reference(
        content=content,
        fingerprint=fingerprint,
    )
    artifacts.store_immutable_bytes(
        artifact_id=str(reference["artifact_id"]),
        version_number=1,
        filename="payload.json",
        content=content,
        expected_sha256=fingerprint,
        media_type=str(reference["media_type"]),
    )
    with Session(engine) as session:
        assert session.exec(select(ArtifactDB)).all() == []
        assert session.exec(select(ArtifactVersionDB)).all() == []

    recovered = ContentAddressedKnowledgeIndexPayloadStore(
        engine=engine,
        artifact_store=ArtifactStore(artifacts.base_dir),
    ).store_payload(
        content=content,
        fingerprint=fingerprint,
        created_by="owner-alice",
    )

    assert recovered == reference
    with Session(engine) as session:
        assert len(session.exec(select(ArtifactDB)).all()) == 1
        assert len(session.exec(select(ArtifactVersionDB)).all()) == 1
