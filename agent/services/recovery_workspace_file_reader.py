"""Safe, bounded reads of Recovery workspace evidence files."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class RecoveryWorkspaceFileReadError(RuntimeError):
    """Raised when a workspace path cannot be read as bounded evidence."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class RecoveryWorkspaceFileSnapshot:
    """Immutable bytes and identity observed through one open descriptor."""

    content: bytes
    resolved_path: Path
    size_bytes: int


class RecoveryWorkspaceFileReader:
    """Read one regular in-workspace file without following symlink escapes."""

    def read(
        self,
        *,
        workspace_root: Path,
        relative_path: str,
        maximum_bytes: int,
    ) -> RecoveryWorkspaceFileSnapshot:
        from agent.services.workspace_path_validator import (
            WorkspacePathValidator,
        )

        if (
            isinstance(maximum_bytes, bool)
            or not isinstance(maximum_bytes, int)
            or maximum_bytes < 0
        ):
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_file_size_limit_invalid"
            )
        try:
            root = workspace_root.resolve(strict=True)
        except OSError as exc:
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_path_unreadable"
            ) from exc
        resolved = WorkspacePathValidator(str(root)).validate(
            relative_path
        )
        if not resolved.ok:
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_path_invalid"
            )
        candidate = Path(resolved.resolved_path)
        current = root
        for part in Path(relative_path).parts:
            current = current / part
            try:
                if current.is_symlink():
                    raise RecoveryWorkspaceFileReadError(
                        "recovery_artifact_symlink_denied"
                    )
            except OSError as exc:
                raise RecoveryWorkspaceFileReadError(
                    "recovery_artifact_path_unreadable"
                ) from exc

        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(str(candidate), flags)
        except OSError as exc:
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_path_unreadable"
            ) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or int(before.st_size) < 0
                or int(before.st_size) > maximum_bytes
            ):
                raise RecoveryWorkspaceFileReadError(
                    "recovery_artifact_file_type_or_size_invalid"
                )
            self._verify_open_file_within_root(
                descriptor=descriptor,
                workspace_root=root,
            )
            chunks: list[bytes] = []
            total = 0
            while True:
                remaining_with_sentinel = maximum_bytes - total + 1
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, remaining_with_sentinel),
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum_bytes:
                    raise RecoveryWorkspaceFileReadError(
                        "recovery_artifact_file_too_large"
                    )
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or total != int(after.st_size)
            ):
                raise RecoveryWorkspaceFileReadError(
                    "recovery_artifact_file_changed_during_read"
                )
            return RecoveryWorkspaceFileSnapshot(
                content=b"".join(chunks),
                resolved_path=candidate,
                size_bytes=total,
            )
        except OSError as exc:
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_path_unreadable"
            ) from exc
        finally:
            os.close(descriptor)

    @staticmethod
    def _verify_open_file_within_root(
        *,
        descriptor: int,
        workspace_root: Path,
    ) -> None:
        fd_path = Path(f"/proc/self/fd/{descriptor}")
        try:
            opened_path = os.readlink(fd_path)
        except OSError as exc:
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_open_file_identity_unavailable"
            ) from exc
        if (
            not os.path.isabs(opened_path)
            or opened_path.endswith(" (deleted)")
        ):
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_open_file_identity_unavailable"
            )
        try:
            actual = Path(opened_path).resolve(strict=True)
            opened_stat = os.stat(actual)
            descriptor_stat = os.fstat(descriptor)
        except OSError as exc:
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_open_file_identity_unavailable"
            ) from exc
        if (
            opened_stat.st_dev != descriptor_stat.st_dev
            or opened_stat.st_ino != descriptor_stat.st_ino
        ):
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_open_file_identity_mismatch"
            )
        try:
            actual.relative_to(workspace_root)
        except ValueError as exc:
            raise RecoveryWorkspaceFileReadError(
                "recovery_artifact_path_escape"
            ) from exc


_READER = RecoveryWorkspaceFileReader()


def get_recovery_workspace_file_reader() -> RecoveryWorkspaceFileReader:
    return _READER


__all__ = [
    "RecoveryWorkspaceFileReadError",
    "RecoveryWorkspaceFileReader",
    "RecoveryWorkspaceFileSnapshot",
    "get_recovery_workspace_file_reader",
]
