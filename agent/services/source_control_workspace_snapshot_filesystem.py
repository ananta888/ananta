"""Descriptor-relative filesystem primitives for workspace snapshot uploads.

All paths below the configured workspace root are opened relative to an
already verified directory descriptor with ``O_NOFOLLOW`` so a concurrent
symlink swap cannot redirect a write. Locking, staging, streaming writes,
publication checks and best-effort cleanup live here; the upload service
only sequences them.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

import fcntl

from agent.services.source_control_workspace_snapshot_contracts import (
    BrowserSnapshotRelativePath,
    WorkspaceSnapshotLimits,
)
from agent.services.source_control_workspace_snapshot_errors import (
    WorkspaceSnapshotUploadError,
)
from agent.sources.git_source_connector_common import GitSourceScope

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_LOCK_FLAGS = (
    os.O_RDWR
    | os.O_CREAT
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_STAGING_PREFIX = ".ananta-snapshot-"
LOCK_NAME = ".ananta-workspace-snapshot.lock"
_CHUNK_BYTES = 128 * 1024


class WorkspaceSnapshotFilesystem:
    """Fail-closed, descriptor-relative storage for one project scope."""

    def __init__(
        self,
        *,
        token_factory: Callable[[], str] = (
            lambda: secrets.token_urlsafe(32)
        ),
    ) -> None:
        self._token_factory = token_factory

    @contextmanager
    def locked_project_root(
        self,
        *,
        workspace_root: Path | None,
        scope: GitSourceScope,
    ) -> Iterator[tuple[Path, int]]:
        base = self.resolved_workspace_root(workspace_root)
        base_fd: int | None = None
        tenant_fd: int | None = None
        project_fd: int | None = None
        lock_fd: int | None = None
        try:
            base_fd = os.open(base, _DIRECTORY_FLAGS)
            tenant_name = hashlib.sha256(
                scope.tenant_id.encode("utf-8")
            ).hexdigest()
            project_name = hashlib.sha256(
                scope.project_id.encode("utf-8")
            ).hexdigest()
            tenant_fd = self.ensure_directory(base_fd, tenant_name)
            project_fd = self.ensure_directory(tenant_fd, project_name)
            lock_fd = os.open(
                LOCK_NAME,
                _LOCK_FLAGS,
                0o600,
                dir_fd=project_fd,
            )
            lock_stat = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_lock_invalid",
                    status_code=409,
                )
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield base / tenant_name / project_name, project_fd
        except OSError as exc:
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_storage_unavailable",
                status_code=503,
            ) from exc
        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(lock_fd)
            if project_fd is not None:
                os.close(project_fd)
            if tenant_fd is not None:
                os.close(tenant_fd)
            if base_fd is not None:
                os.close(base_fd)

    @staticmethod
    def resolved_workspace_root(configured: Path | None) -> Path:
        if configured is None:
            raise WorkspaceSnapshotUploadError(
                "workspace_root_unavailable",
                status_code=503,
            )
        if configured.is_symlink():
            raise WorkspaceSnapshotUploadError(
                "workspace_root_invalid",
                status_code=503,
            )
        try:
            resolved = configured.resolve(strict=True)
        except OSError as exc:
            raise WorkspaceSnapshotUploadError(
                "workspace_root_unavailable",
                status_code=503,
            ) from exc
        if not resolved.is_dir():
            raise WorkspaceSnapshotUploadError(
                "workspace_root_invalid",
                status_code=503,
            )
        return resolved

    @staticmethod
    def ensure_directory(parent_fd: int, name: str) -> int:
        created = False
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode):
            raise WorkspaceSnapshotUploadError(
                "workspace_scope_root_invalid",
                status_code=409,
            )
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            os.close(descriptor)
            raise WorkspaceSnapshotUploadError(
                "workspace_scope_root_changed",
                status_code=409,
            )
        if created:
            os.fsync(parent_fd)
        return descriptor

    def create_staging(self, project_fd: int) -> tuple[str, int]:
        for attempt in range(4):
            token = hashlib.sha256(
                f"{self._token_factory()}\0{attempt}".encode("utf-8")
            ).hexdigest()
            name = f"{_STAGING_PREFIX}{token}.partial"
            try:
                os.mkdir(name, 0o700, dir_fd=project_fd)
                try:
                    descriptor = os.open(
                        name,
                        _DIRECTORY_FLAGS,
                        dir_fd=project_fd,
                    )
                except Exception:
                    os.rmdir(name, dir_fd=project_fd)
                    raise
                os.fsync(project_fd)
                return name, descriptor
            except FileExistsError:
                continue
        raise WorkspaceSnapshotUploadError(
            "workspace_snapshot_staging_collision",
            status_code=503,
        )

    def write_file(
        self,
        *,
        stage_fd: int,
        relative: BrowserSnapshotRelativePath,
        stream: BinaryIO,
        total_before: int,
        limits: WorkspaceSnapshotLimits,
    ) -> tuple[int, str]:
        directory_fds: list[int] = []
        parent_fd = stage_fd
        file_fd: int | None = None
        try:
            for component in relative.parts[:-1]:
                child_fd = self.ensure_directory(parent_fd, component)
                directory_fds.append(child_fd)
                parent_fd = child_fd
            try:
                file_fd = os.open(
                    relative.parts[-1],
                    _FILE_CREATE_FLAGS,
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError as exc:
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_case_collision",
                    status_code=409,
                ) from exc
            digest = hashlib.sha256()
            byte_size = 0
            while True:
                chunk = stream.read(_CHUNK_BYTES)
                if chunk in (b"", None):
                    break
                if not isinstance(chunk, bytes):
                    raise WorkspaceSnapshotUploadError(
                        "workspace_snapshot_file_stream_invalid"
                    )
                byte_size += len(chunk)
                if byte_size > limits.max_file_bytes:
                    raise WorkspaceSnapshotUploadError(
                        "workspace_snapshot_file_bytes_exceeded",
                        status_code=413,
                    )
                if total_before + byte_size > limits.max_total_bytes:
                    raise WorkspaceSnapshotUploadError(
                        "workspace_snapshot_total_bytes_exceeded",
                        status_code=413,
                    )
                digest.update(chunk)
                self.write_all(file_fd, chunk)
            if byte_size == 0:
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_empty_file_denied"
                )
            metadata = os.fstat(file_fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size != byte_size
            ):
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_special_file_denied",
                    status_code=409,
                )
            os.fsync(file_fd)
            return byte_size, digest.hexdigest()
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_symlink_denied",
                    status_code=409,
                ) from exc
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_write_failed",
                status_code=507,
            ) from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            for descriptor in reversed(directory_fds):
                self.fsync_quiet(descriptor)
                os.close(descriptor)

    @staticmethod
    def write_all(descriptor: int, value: bytes) -> None:
        view = memoryview(value)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            offset += written

    @staticmethod
    def publish_staging(
        *,
        project_fd: int,
        stage_name: str,
        final_name: str,
    ) -> None:
        os.rename(
            stage_name,
            final_name,
            src_dir_fd=project_fd,
            dst_dir_fd=project_fd,
        )

    @staticmethod
    def published_exists(
        *,
        project_fd: int,
        stage_name: str,
        final_name: str,
    ) -> bool:
        target_key = final_name.casefold()
        exact = False
        for name in os.listdir(project_fd):
            if name in {stage_name, LOCK_NAME}:
                continue
            if name.casefold() != target_key:
                continue
            if name != final_name:
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_case_collision",
                    status_code=409,
                )
            metadata = os.stat(
                name,
                dir_fd=project_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(metadata.st_mode):
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_publish_conflict",
                    status_code=409,
                )
            exact = True
        return exact

    @staticmethod
    def remove_tree(path: Path) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        except OSError:
            return
        try:
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(
                metadata.st_mode
            ):
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def fsync_quiet(descriptor: int) -> None:
        try:
            os.fsync(descriptor)
        except OSError:
            pass


__all__ = ["LOCK_NAME", "WorkspaceSnapshotFilesystem"]
