from __future__ import annotations

import ctypes
import errno
import hashlib
import mimetypes
import os
import re
import stat
import uuid
from collections.abc import Callable
from pathlib import Path

from agent.config import settings


class ArtifactStore:
    """Filesystem-backed raw artifact storage."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or Path(settings.data_dir) / "artifacts").resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def store_bytes(
        self,
        *,
        artifact_id: str,
        version_number: int,
        filename: str,
        content: bytes,
        media_type: str | None = None,
        execution_checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        checkpoint = execution_checkpoint or (lambda: None)
        checkpoint()
        safe_filename = Path(filename or "artifact.bin").name or "artifact.bin"
        artifact_dir = self.base_dir / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        storage_name = f"v{version_number:04d}__{safe_filename}"
        storage_path = artifact_dir / storage_name
        if execution_checkpoint is None:
            storage_path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            detected_media_type = (
                media_type
                or mimetypes.guess_type(safe_filename)[0]
                or "application/octet-stream"
            )
            return {
                "storage_path": str(storage_path),
                "sha256": digest,
                "size_bytes": len(content),
                "media_type": detected_media_type,
                "filename": safe_filename,
            }

        # A governed write is staged beside its destination. A deadline or
        # process error can therefore remove only the incomplete temporary
        # file, while os.replace preserves the store's existing overwrite and
        # idempotent-replay contract.
        temporary_path = artifact_dir / (
            f".{storage_name}.tmp-{uuid.uuid4().hex}"
        )
        hasher = hashlib.sha256()
        try:
            with temporary_path.open("xb") as handle:
                view = memoryview(content)
                for offset in range(0, len(view), 1024 * 1024):
                    checkpoint()
                    chunk = view[offset : offset + 1024 * 1024]
                    handle.write(chunk)
                    hasher.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            checkpoint()
            os.replace(temporary_path, storage_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        digest = hasher.hexdigest()
        detected_media_type = media_type or mimetypes.guess_type(safe_filename)[0] or "application/octet-stream"
        return {
            "storage_path": str(storage_path),
            "sha256": digest,
            "size_bytes": len(content),
            "media_type": detected_media_type,
            "filename": safe_filename,
        }

    def store_immutable_bytes(
        self,
        *,
        artifact_id: str,
        version_number: int,
        filename: str,
        content: bytes,
        expected_sha256: str,
        media_type: str = "application/octet-stream",
    ) -> dict:
        """Create a content-addressed artifact once and never overwrite it."""

        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", artifact_id)
            is None
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or hashlib.sha256(content).hexdigest() != expected_sha256
        ):
            raise ValueError("immutable_artifact_identity_invalid")
        safe_filename = Path(filename or "artifact.bin").name
        if not safe_filename or safe_filename != filename:
            raise ValueError("immutable_artifact_filename_invalid")
        artifact_dir = self.base_dir / artifact_id
        storage_name = f"v{version_number:04d}__{safe_filename}"
        storage_path = artifact_dir / storage_name
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        create_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        read_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        base_fd: int | None = None
        artifact_fd: int | None = None
        temporary_name: str | None = None
        try:
            try:
                base_fd = os.open(self.base_dir, directory_flags)
                try:
                    os.mkdir(artifact_id, mode=0o700, dir_fd=base_fd)
                    os.fsync(base_fd)
                except FileExistsError:
                    pass
                artifact_fd = os.open(
                    artifact_id, directory_flags, dir_fd=base_fd
                )
                try:
                    descriptor = os.open(
                        storage_name,
                        read_flags,
                        dir_fd=artifact_fd,
                    )
                except FileNotFoundError:
                    descriptor = None
                if descriptor is not None:
                    existing = self._read_immutable_descriptor(
                        descriptor,
                        expected_sha256=expected_sha256,
                        expected_size=len(content),
                    )
                    if existing != content:
                        raise ValueError("immutable_artifact_conflict")
                else:
                    temporary_name = (
                        f".{storage_name}.tmp-{uuid.uuid4().hex}"
                    )
                    descriptor = os.open(
                        temporary_name,
                        create_flags,
                        0o600,
                        dir_fd=artifact_fd,
                    )
                    try:
                        with os.fdopen(descriptor, "wb") as handle:
                            handle.write(content)
                            handle.flush()
                            os.fsync(handle.fileno())
                    except BaseException:
                        try:
                            os.unlink(temporary_name, dir_fd=artifact_fd)
                        except OSError:
                            pass
                        raise
                    try:
                        self._rename_noreplace(
                            temporary_name,
                            storage_name,
                            directory_fd=artifact_fd,
                        )
                        temporary_name = None
                        os.fsync(artifact_fd)
                    except FileExistsError:
                        existing_descriptor = os.open(
                            storage_name,
                            read_flags,
                            dir_fd=artifact_fd,
                        )
                        existing = self._read_immutable_descriptor(
                            existing_descriptor,
                            expected_sha256=expected_sha256,
                            expected_size=len(content),
                        )
                        if existing != content:
                            raise ValueError("immutable_artifact_conflict")
            except OSError as exc:
                raise ValueError("immutable_artifact_unavailable") from exc
        finally:
            if temporary_name is not None and artifact_fd is not None:
                try:
                    os.unlink(temporary_name, dir_fd=artifact_fd)
                except FileNotFoundError:
                    pass
            if artifact_fd is not None:
                os.close(artifact_fd)
            if base_fd is not None:
                os.close(base_fd)
        return {
            "storage_path": str(storage_path),
            "sha256": expected_sha256,
            "size_bytes": len(content),
            "media_type": media_type,
            "filename": safe_filename,
        }

    @staticmethod
    def _rename_noreplace(
        source: str,
        destination: str,
        *,
        directory_fd: int,
    ) -> None:
        """Atomically publish a complete file without replacing a peer."""

        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(
                errno.ENOTSUP,
                "atomic no-replace rename is unavailable",
            )
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            directory_fd,
            os.fsencode(source),
            directory_fd,
            os.fsencode(destination),
            1,  # RENAME_NOREPLACE
        )
        if result != 0:
            error_number = ctypes.get_errno()
            raise OSError(
                error_number,
                os.strerror(error_number),
                destination,
            )

    def load_immutable_bytes(
        self,
        *,
        artifact_id: str,
        version_number: int,
        filename: str,
        expected_sha256: str,
        expected_size: int,
    ) -> bytes:
        safe_filename = Path(filename or "").name
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", artifact_id)
            is None
            or version_number < 1
            or safe_filename != filename
            or expected_size < 0
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        ):
            raise ValueError("immutable_artifact_reference_invalid")
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        read_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        base_fd: int | None = None
        artifact_fd: int | None = None
        try:
            base_fd = os.open(self.base_dir, directory_flags)
            artifact_fd = os.open(
                artifact_id, directory_flags, dir_fd=base_fd
            )
            descriptor = os.open(
                f"v{version_number:04d}__{safe_filename}",
                read_flags,
                dir_fd=artifact_fd,
            )
            return self._read_immutable_descriptor(
                descriptor,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )
        except OSError as exc:
            raise ValueError("immutable_artifact_unavailable") from exc
        finally:
            if artifact_fd is not None:
                os.close(artifact_fd)
            if base_fd is not None:
                os.close(base_fd)

    @staticmethod
    def _read_immutable_descriptor(
        descriptor: int,
        *,
        expected_sha256: str,
        expected_size: int,
    ) -> bytes:
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            content = handle.read(expected_size + 1)
            after = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_ino != after.st_ino
            or before.st_size != expected_size
            or after.st_size != expected_size
            or len(content) != expected_size
            or hashlib.sha256(content).hexdigest() != expected_sha256
        ):
            raise ValueError("immutable_artifact_integrity_failed")
        return content


artifact_store = ArtifactStore()


def get_artifact_store() -> ArtifactStore:
    return artifact_store
