"""Bounded local-byte resolution for outputs of one delegated Native task."""

import hashlib
import os
import re
import stat
from pathlib import Path

from ananta_contracts.native_artifact_transfer import MAX_ARTIFACT_BYTES, NativeArtifactBytes
from ananta_contracts.native_context_bundle import native_context_digest


class LocalNativeArtifactReader:
    def __init__(self, *, storage_root, load_artifact, load_version):
        self._root = Path(storage_root).absolute()
        self._load_artifact, self._load_version = load_artifact, load_version

    def read(self, *, command, task, output_name, reference):
        if not isinstance(reference, str) or re.fullmatch(r"artifact://[A-Za-z0-9_.-]{1,128}", reference) is None:
            raise ValueError("native_artifact_local_reference_invalid")
        artifact = self._load_artifact(reference.removeprefix("artifact://"))
        expected = {
            "hub_task_id": task["id"], "tenant_id": command.tenant_id, "project_id": task.get("project_id"),
            "command_digest": native_context_digest(command.to_dict()), "attempt_id": command.attempt_id,
            "fencing_token": command.fencing_token, "output_name": output_name,
        }
        if (
            artifact is None or artifact.id != reference.removeprefix("artifact://")
            or artifact.status != "stored" or not artifact.latest_version_id
            or native_context_digest((artifact.artifact_metadata or {}).get("native_output_binding"))
            != native_context_digest(expected)
        ):
            raise ValueError("native_artifact_local_binding_invalid")
        version = self._load_version(artifact.latest_version_id)
        if (
            version is None or version.artifact_id != artifact.id
            or type(version.size_bytes) is not int or not 0 <= version.size_bytes <= MAX_ARTIFACT_BYTES
            or version.sha256 != artifact.latest_sha256
        ):
            raise ValueError("native_artifact_local_version_invalid")
        content = self._read_bounded(version.storage_path)
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != version.size_bytes or digest != version.sha256:
            raise ValueError("native_artifact_local_content_mismatch")
        return NativeArtifactBytes(output_name, version.original_filename, version.media_type, content, digest)

    def _read_bounded(self, storage_path):
        return read_confined_native_bytes(self._root, storage_path)


def read_confined_native_bytes(storage_root, storage_path):
    """Read one bounded regular file using descriptor-relative no-follow opens."""
    root = Path(storage_root).absolute()
    return _ConfinedNativeBytes(root).read(storage_path)


class _ConfinedNativeBytes:
    def __init__(self, root):
        self._root = root

    def read(self, storage_path):
        try:
            relative = Path(storage_path).absolute().relative_to(self._root)
            if not relative.parts or any(part in {".", ".."} for part in relative.parts):
                raise ValueError("path")
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            directory = os.open(self._root, flags | os.O_DIRECTORY)
            try:
                for part in relative.parts[:-1]:
                    next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
                    os.close(directory)
                    directory = next_directory
                descriptor = os.open(relative.parts[-1], flags | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(descriptor, "rb") as handle:
                    metadata = os.fstat(handle.fileno())
                    if (
                        not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                        or metadata.st_size > MAX_ARTIFACT_BYTES
                    ):
                        raise ValueError("file")
                    content = handle.read(MAX_ARTIFACT_BYTES + 1)
                    if len(content) > MAX_ARTIFACT_BYTES:
                        raise ValueError("size")
                    return content
            finally:
                os.close(directory)
        except (OSError, ValueError) as exc:
            raise ValueError("native_artifact_local_path_denied") from exc
