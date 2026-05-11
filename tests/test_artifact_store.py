"""AFH-T013: Artifact persistence tests."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from agent.services.artifact_store import ArtifactStore


class TestArtifactStorePersistence:
    def test_store_bytes_returns_metadata(self, tmp_path: Path) -> None:
        store = ArtifactStore(base_dir=tmp_path / "artifacts")
        content = b"hello artifact"
        result = store.store_bytes(
            artifact_id="art-001",
            version_number=1,
            filename="output.txt",
            content=content,
        )
        assert result["sha256"] == hashlib.sha256(content).hexdigest()
        assert result["size_bytes"] == len(content)
        assert Path(result["storage_path"]).exists()
        assert result["filename"] == "output.txt"

    def test_store_bytes_creates_artifact_dir(self, tmp_path: Path) -> None:
        store = ArtifactStore(base_dir=tmp_path / "artifacts")
        store.store_bytes(artifact_id="art-002", version_number=1, filename="f.py", content=b"x")
        assert (tmp_path / "artifacts" / "art-002").is_dir()

    def test_store_bytes_rejects_path_traversal_filename(self, tmp_path: Path) -> None:
        store = ArtifactStore(base_dir=tmp_path / "artifacts")
        # Path traversal in filename must be sanitized
        result = store.store_bytes(
            artifact_id="art-003",
            version_number=1,
            filename="../../../etc/passwd",
            content=b"attack",
        )
        # The sanitized filename must not contain ../
        assert "../" not in result["filename"], (
            f"Filename must not contain path traversal: {result['filename']!r}"
        )
        # And the file must be stored in the artifact dir, not escaped
        storage = Path(result["storage_path"])
        assert storage.is_relative_to(tmp_path.resolve()), (
            "Storage path must be inside artifact base dir"
        )

    def test_store_bytes_detects_media_type(self, tmp_path: Path) -> None:
        store = ArtifactStore(base_dir=tmp_path / "artifacts")
        result = store.store_bytes(artifact_id="art-004", version_number=1, filename="app.py", content=b"code")
        assert "python" in result["media_type"].lower() or result["media_type"] == "text/x-python"

    def test_store_multiple_versions(self, tmp_path: Path) -> None:
        store = ArtifactStore(base_dir=tmp_path / "artifacts")
        v1 = store.store_bytes(artifact_id="art-005", version_number=1, filename="f.txt", content=b"v1")
        v2 = store.store_bytes(artifact_id="art-005", version_number=2, filename="f.txt", content=b"v2")
        assert v1["storage_path"] != v2["storage_path"]
        assert v1["sha256"] != v2["sha256"]
