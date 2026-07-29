from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from worker.training.model_imports import (
    ModelFileMetadata,
    ModelImportCommand,
    ModelImportError,
    UnslothModelImportExecutor,
)


def tree_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


class FakeDownloads:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.download_calls = 0

    def list_files(self, *, model_id: str, revision: str):
        return [
            ModelFileMetadata(path=name, size=len(content))
            for name, content in self.files.items()
        ]

    def download(
        self,
        *,
        model_id: str,
        revision: str,
        destination: Path,
        allow_patterns: tuple[str, ...],
    ) -> None:
        self.download_calls += 1
        for name, content in self.files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def command(expected_sha256: str, *, max_bytes: int = 1024) -> ModelImportCommand:
    return ModelImportCommand(
        tenant_id="tenant-a",
        project_id="project-a",
        source_id="source-a",
        kind="huggingface_snapshot",
        expected_sha256=expected_sha256,
        artifact_id=None,
        model_id="org/model",
        revision="c" * 40,
        max_bytes=max_bytes,
        allow_patterns=("*.safetensors",),
        trust_remote_code=False,
        network_authorized=True,
        license_status="approved",
    )


def test_download_is_pinned_hash_checked_and_cached(tmp_path: Path) -> None:
    files = {"model.safetensors": b"weights"}
    downloads = FakeDownloads(files)
    executor = UnslothModelImportExecutor(
        cache_root=tmp_path / "cache",
        artifact_root=tmp_path / "artifacts",
        downloads=downloads,
        network_enabled=True,
    )
    request = command(tree_hash(files))

    first = executor.execute(request)
    second = executor.execute(request)

    assert first == second
    assert downloads.download_calls == 1


def test_download_size_is_rejected_before_materialization(tmp_path: Path) -> None:
    files = {"model.safetensors": b"oversized"}
    downloads = FakeDownloads(files)
    executor = UnslothModelImportExecutor(
        cache_root=tmp_path / "cache",
        artifact_root=tmp_path / "artifacts",
        downloads=downloads,
        network_enabled=True,
    )

    with pytest.raises(ModelImportError) as error:
        executor.execute(command(tree_hash(files), max_bytes=2))

    assert error.value.code == "model_download_size_exceeded"
    assert downloads.download_calls == 0


def test_hash_mismatch_removes_staging_directory(tmp_path: Path) -> None:
    downloads = FakeDownloads({"model.safetensors": b"weights"})
    cache_root = tmp_path / "cache"
    executor = UnslothModelImportExecutor(
        cache_root=cache_root,
        artifact_root=tmp_path / "artifacts",
        downloads=downloads,
        network_enabled=True,
    )

    with pytest.raises(ModelImportError) as error:
        executor.execute(command("0" * 64))

    assert error.value.code == "model_import_hash_mismatch"
    assert list(cache_root.iterdir()) == []
