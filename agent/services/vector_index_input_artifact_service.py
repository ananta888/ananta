"""Content-addressed Hub publisher for delegated vector-index inputs."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocationError,
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_store_contract import VectorScope

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_MAXIMUM_BYTES = 64 * 1024 * 1024


class VectorIndexInputPublishError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "vector_index_input_publish_failed")
        super().__init__(self.reason)


class FilesystemVectorIndexInputPublisher:
    """Publish immutable task inputs into an explicitly shared artifact root.

    The Hub is the only writer. Workers receive only a relative, digest-bound
    reference and mount the same root read-only. Scope values are represented
    by a hash so tenant identifiers never leak into filesystem paths.
    """

    def __init__(
        self,
        *,
        publish_root: str | Path,
        maximum_bytes: int = _DEFAULT_MAXIMUM_BYTES,
    ) -> None:
        root = Path(publish_root)
        if not root.is_absolute():
            raise ValueError("vector_index_input_publish_root_must_be_absolute")
        if root.exists() and root.is_symlink():
            raise ValueError("vector_index_input_publish_root_symlink_forbidden")
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise ValueError("vector_index_input_publish_root_unavailable") from exc
        if not resolved.is_dir():
            raise ValueError("vector_index_input_publish_root_not_directory")
        self._root = resolved
        self._maximum_bytes = max(1, int(maximum_bytes))

    def publish(
        self,
        *,
        scope: VectorScope,
        content: bytes,
        content_sha256: str,
    ) -> Mapping[str, Any]:
        if not isinstance(scope, VectorScope):
            raise VectorIndexInputPublishError("vector_index_input_publish_scope_invalid")
        if not isinstance(content, bytes):
            raise VectorIndexInputPublishError("vector_index_input_publish_content_invalid")
        if not content or len(content) > self._maximum_bytes:
            raise VectorIndexInputPublishError("vector_index_input_publish_content_too_large")
        expected_digest = str(content_sha256 or "").strip().lower()
        if _SHA256.fullmatch(expected_digest) is None:
            raise VectorIndexInputPublishError("vector_index_input_publish_digest_invalid")
        actual_digest = hashlib.sha256(content).hexdigest()
        if actual_digest != expected_digest:
            raise VectorIndexInputPublishError("vector_index_input_publish_digest_mismatch")

        try:
            location = VectorIndexArtifactLocator.locate(
                scope=scope,
                content_sha256=actual_digest,
            )
        except VectorIndexArtifactLocationError as exc:
            raise VectorIndexInputPublishError(exc.reason) from exc
        relative = Path(location.path)
        target = self._root / relative
        self._ensure_safe_parent(target.parent)

        if target.exists():
            self._verify_existing(target, expected_digest)
            return location.to_reference()

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".vector-index-input-",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as handle:
                temporary_name = handle.name
                os.chmod(temporary_name, 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
            temporary_name = None
            os.chmod(target, 0o600)
            self._sync_directory(target.parent)
        except OSError as exc:
            raise VectorIndexInputPublishError("vector_index_input_publish_write_failed") from exc
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass

        return location.to_reference()

    def _ensure_safe_parent(self, parent: Path) -> None:
        relative = parent.relative_to(self._root)
        current = self._root
        for part in relative.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise VectorIndexInputPublishError("vector_index_input_publish_symlink_forbidden")
            try:
                current.mkdir(exist_ok=True, mode=0o700)
            except OSError as exc:
                raise VectorIndexInputPublishError("vector_index_input_publish_write_failed") from exc
        try:
            resolved = parent.resolve(strict=True)
        except OSError as exc:
            raise VectorIndexInputPublishError("vector_index_input_publish_write_failed") from exc
        if not resolved.is_relative_to(self._root):
            raise VectorIndexInputPublishError("vector_index_input_publish_path_escape")

    def _verify_existing(self, target: Path, expected_digest: str) -> None:
        if target.is_symlink() or not target.is_file():
            raise VectorIndexInputPublishError("vector_index_input_publish_existing_invalid")
        try:
            with target.open("rb") as handle:
                raw = handle.read(self._maximum_bytes + 1)
        except OSError as exc:
            raise VectorIndexInputPublishError("vector_index_input_publish_existing_unreadable") from exc
        if len(raw) > self._maximum_bytes:
            raise VectorIndexInputPublishError("vector_index_input_publish_existing_too_large")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_digest:
            raise VectorIndexInputPublishError("vector_index_input_publish_existing_digest_mismatch")

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        descriptor: int | None = None
        try:
            descriptor = os.open(directory, os.O_RDONLY)
            os.fsync(descriptor)
        finally:
            if descriptor is not None:
                os.close(descriptor)


def build_vector_index_input_publisher(
    *,
    environ: Mapping[str, str] | None = None,
) -> FilesystemVectorIndexInputPublisher | None:
    values = environ if environ is not None else os.environ
    root = str(values.get("ANANTA_VECTOR_INDEX_INPUT_PUBLISH_ROOT") or "").strip()
    if not root:
        return None
    return FilesystemVectorIndexInputPublisher(publish_root=root)


__all__ = [
    "FilesystemVectorIndexInputPublisher",
    "VectorIndexInputPublishError",
    "build_vector_index_input_publisher",
]
