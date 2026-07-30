from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.model_intelligence_artifact_store import (
    FileSystemModelIntelligenceArtifactStore,
    ModelIntelligenceArtifactStoreError,
)


def test_content_addressing_is_deterministic_and_tenant_bound(tmp_path: Path) -> None:
    store = FileSystemModelIntelligenceArtifactStore(root=tmp_path / "objects")
    content = b'{"schema":"fixture.v1"}\n'

    first = store.put_bytes(
        "tenant-a",
        content,
        media_type="application/json",
    )
    repeated = store.put_bytes(
        "tenant-a",
        content,
        media_type="application/json",
    )
    other_tenant = store.put_bytes(
        "tenant-b",
        content,
        media_type="application/json",
    )

    assert first == repeated
    assert first.digest == other_tenant.digest
    assert first.tenant_scope != other_tenant.tenant_scope
    assert store.get_bytes("tenant-a", first) == content


def test_foreign_tenant_cannot_read_content_or_metadata(tmp_path: Path) -> None:
    store = FileSystemModelIntelligenceArtifactStore(root=tmp_path / "objects")
    reference = store.put_bytes(
        "tenant-a",
        b"private",
        media_type="application/octet-stream",
    )

    with pytest.raises(ModelIntelligenceArtifactStoreError) as content_error:
        store.get_bytes("tenant-b", reference)
    with pytest.raises(ModelIntelligenceArtifactStoreError) as metadata_error:
        store.get_metadata("tenant-b", reference)

    assert content_error.value.reason_code == "artifact_access_denied"
    assert metadata_error.value.reason_code == "artifact_access_denied"


def test_expected_digest_and_read_integrity_are_enforced(tmp_path: Path) -> None:
    store = FileSystemModelIntelligenceArtifactStore(root=tmp_path / "objects")

    with pytest.raises(ModelIntelligenceArtifactStoreError) as error:
        store.put_bytes(
            "tenant-a",
            b"content",
            media_type="application/octet-stream",
            expected_digest="sha256:" + ("0" * 64),
        )

    assert error.value.reason_code == "artifact_digest_mismatch"


def test_delete_is_idempotent_and_reports_whether_object_existed(tmp_path: Path) -> None:
    store = FileSystemModelIntelligenceArtifactStore(root=tmp_path / "objects")
    reference = store.put_bytes(
        "tenant-a",
        b"retained-until-deletion",
        media_type="application/octet-stream",
    )

    assert store.delete("tenant-a", reference) is True
    assert store.delete("tenant-a", reference) is False

    with pytest.raises(ModelIntelligenceArtifactStoreError) as missing:
        store.get_bytes("tenant-a", reference)
    assert missing.value.reason_code == "artifact_not_found"


def test_delete_rejects_foreign_tenant_before_touching_object(tmp_path: Path) -> None:
    store = FileSystemModelIntelligenceArtifactStore(root=tmp_path / "objects")
    reference = store.put_bytes(
        "tenant-a",
        b"tenant-private",
        media_type="application/octet-stream",
    )

    with pytest.raises(ModelIntelligenceArtifactStoreError) as denied:
        store.delete("tenant-b", reference)

    assert denied.value.reason_code == "artifact_access_denied"
    assert store.get_bytes("tenant-a", reference) == b"tenant-private"
