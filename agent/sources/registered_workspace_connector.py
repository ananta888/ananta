"""Registered workspace and local-directory connector with bounded inventory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping, Protocol


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")


class RegisteredWorkspaceError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class RegisteredWorkspace:
    workspace_id: str
    tenant_id: str
    project_id: str
    root: Path
    enabled: bool
    read_only: bool
    owner_id: str | None = None


class RegisteredWorkspaceCatalogPort(Protocol):
    def get(
        self,
        *,
        workspace_id: str,
        tenant_id: str,
        project_id: str,
    ) -> RegisteredWorkspace | None: ...


class WorkspaceFileInspectionPort(Protocol):
    def stat(
        self, path: Path, *, follow_symlinks: bool
    ) -> os.stat_result: ...

    def lstat(self, path: Path) -> os.stat_result: ...

    def hash_file(
        self, path: Path, *, before: os.stat_result
    ) -> str: ...


class PosixWorkspaceFileInspection:
    """Narrow injectable seam for deterministic race enforcement."""

    def stat(
        self, path: Path, *, follow_symlinks: bool
    ) -> os.stat_result:
        return path.stat(follow_symlinks=follow_symlinks)

    def lstat(self, path: Path) -> os.stat_result:
        return path.lstat()

    def hash_file(
        self, path: Path, *, before: os.stat_result
    ) -> str:
        return _hash_file_no_follow(path, before=before)


@dataclass(frozen=True)
class WorkspaceInventoryLimits:
    max_files: int = 20_000
    max_total_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 16 * 1024 * 1024
    max_depth: int = 32

    def __post_init__(self) -> None:
        if (
            self.max_files < 1
            or self.max_total_bytes < 1
            or self.max_file_bytes < 1
            or self.max_depth < 1
        ):
            raise RegisteredWorkspaceError("workspace_limits_invalid")


@dataclass(frozen=True)
class WorkspaceFileManifestEntry:
    relative_path: str
    byte_size: int
    content_digest: str
    file_type: str


@dataclass(frozen=True)
class WorkspaceInventoryManifest:
    workspace_id: str
    relative_root: str
    entries: tuple[WorkspaceFileManifestEntry, ...]
    total_bytes: int
    manifest_digest: str
    revision_digest: str


@dataclass(frozen=True)
class DelegatedWorkspaceReference:
    schema: str
    authority: str
    workspace_id: str
    relative_root: str
    manifest_digest: str
    revision_digest: str
    assignment_id: str
    lease_id: str
    access_mode: str


class RegisteredWorkspaceConnector:
    def __init__(
        self,
        *,
        catalog: RegisteredWorkspaceCatalogPort,
        limits: WorkspaceInventoryLimits | None = None,
        inspection: WorkspaceFileInspectionPort | None = None,
    ) -> None:
        self._catalog = catalog
        self._limits = limits or WorkspaceInventoryLimits()
        self._inspection = inspection or PosixWorkspaceFileInspection()

    def inventory(
        self,
        *,
        tenant_id: str,
        project_id: str,
        workspace_id: str,
        relative_path: str = ".",
    ) -> WorkspaceInventoryManifest:
        workspace = self._resolve_workspace(
            tenant_id=tenant_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        root, target, normalized_relative = _resolve_registered_path(
            workspace,
            relative_path,
        )
        root_stat = self._inspection.stat(root, follow_symlinks=False)
        target_stat = self._inspection.stat(target, follow_symlinks=False)
        if root_stat.st_dev != target_stat.st_dev:
            raise RegisteredWorkspaceError("workspace_mount_changed")
        if not stat.S_ISDIR(target_stat.st_mode):
            raise RegisteredWorkspaceError("workspace_directory_required")

        entries: list[WorkspaceFileManifestEntry] = []
        total_bytes = 0
        for directory, dirnames, filenames in os.walk(
            target,
            topdown=True,
            followlinks=False,
        ):
            directory_path = Path(directory)
            directory_stat = self._inspection.lstat(directory_path)
            if directory_stat.st_dev != root_stat.st_dev:
                raise RegisteredWorkspaceError("workspace_mount_changed")
            if not stat.S_ISDIR(directory_stat.st_mode):
                raise RegisteredWorkspaceError(
                    "workspace_special_file_forbidden"
                )
            relative_directory = directory_path.relative_to(target)
            if len(relative_directory.parts) > self._limits.max_depth:
                raise RegisteredWorkspaceError("workspace_depth_budget_exceeded")
            dirnames.sort()
            filenames.sort()
            for dirname in tuple(dirnames):
                child = directory_path / dirname
                child_stat = self._inspection.lstat(child)
                if stat.S_ISLNK(child_stat.st_mode):
                    raise RegisteredWorkspaceError("workspace_symlink_forbidden")
                if not stat.S_ISDIR(child_stat.st_mode):
                    raise RegisteredWorkspaceError(
                        "workspace_special_file_forbidden"
                    )
                if child_stat.st_dev != root_stat.st_dev:
                    raise RegisteredWorkspaceError("workspace_mount_changed")
            for filename in filenames:
                path = directory_path / filename
                entry = self._inspect_file(
                    root=root,
                    target=target,
                    path=path,
                    expected_device=root_stat.st_dev,
                )
                entries.append(entry)
                total_bytes += entry.byte_size
                if len(entries) > self._limits.max_files:
                    raise RegisteredWorkspaceError(
                        "workspace_file_count_budget_exceeded"
                    )
                if total_bytes > self._limits.max_total_bytes:
                    raise RegisteredWorkspaceError(
                        "workspace_total_bytes_budget_exceeded"
                    )
        entries.sort(key=lambda entry: entry.relative_path)
        manifest_payload = [
            {
                "relative_path": entry.relative_path,
                "byte_size": entry.byte_size,
                "content_digest": entry.content_digest,
                "file_type": entry.file_type,
            }
            for entry in entries
        ]
        manifest_digest = _digest(manifest_payload)
        revision_digest = _digest(
            {
                "workspace_id": workspace.workspace_id,
                "relative_root": normalized_relative,
                "manifest_digest": manifest_digest,
            }
        )
        return WorkspaceInventoryManifest(
            workspace_id=workspace.workspace_id,
            relative_root=normalized_relative,
            entries=tuple(entries),
            total_bytes=total_bytes,
            manifest_digest=manifest_digest,
            revision_digest=revision_digest,
        )

    def delegated_reference(
        self,
        *,
        manifest: WorkspaceInventoryManifest,
        assignment_id: str,
        lease_id: str,
    ) -> DelegatedWorkspaceReference:
        for name, value in (
            ("assignment_id", assignment_id),
            ("lease_id", lease_id),
        ):
            if not _OPAQUE_ID.fullmatch(str(value or "")):
                raise RegisteredWorkspaceError(f"{name}_invalid")
        return DelegatedWorkspaceReference(
            schema="ananta.source-control.workspace-reference.v1",
            authority="hub",
            workspace_id=manifest.workspace_id,
            relative_root=manifest.relative_root,
            manifest_digest=manifest.manifest_digest,
            revision_digest=manifest.revision_digest,
            assignment_id=assignment_id,
            lease_id=lease_id,
            access_mode="read_only",
        )

    def _resolve_workspace(
        self,
        *,
        tenant_id: str,
        project_id: str,
        workspace_id: str,
    ) -> RegisteredWorkspace:
        for name, value in (
            ("tenant_id", tenant_id),
            ("project_id", project_id),
            ("workspace_id", workspace_id),
        ):
            if not _OPAQUE_ID.fullmatch(str(value or "")):
                raise RegisteredWorkspaceError(f"{name}_invalid")
        workspace = self._catalog.get(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            project_id=project_id,
        )
        if workspace is None:
            raise RegisteredWorkspaceError("workspace_not_found")
        if (
            workspace.workspace_id != workspace_id
            or workspace.tenant_id != tenant_id
            or workspace.project_id != project_id
        ):
            raise RegisteredWorkspaceError("workspace_not_found")
        if not workspace.enabled:
            raise RegisteredWorkspaceError("workspace_disabled")
        if not workspace.read_only:
            raise RegisteredWorkspaceError("workspace_read_only_required")
        return workspace

    def _inspect_file(
        self,
        *,
        root: Path,
        target: Path,
        path: Path,
        expected_device: int,
    ) -> WorkspaceFileManifestEntry:
        before = self._inspection.lstat(path)
        if stat.S_ISLNK(before.st_mode):
            raise RegisteredWorkspaceError("workspace_symlink_forbidden")
        if not stat.S_ISREG(before.st_mode):
            raise RegisteredWorkspaceError("workspace_special_file_forbidden")
        if before.st_dev != expected_device:
            raise RegisteredWorkspaceError("workspace_mount_changed")
        if before.st_nlink != 1:
            raise RegisteredWorkspaceError("workspace_hardlink_forbidden")
        if before.st_size > self._limits.max_file_bytes:
            raise RegisteredWorkspaceError("workspace_file_bytes_budget_exceeded")
        allocated = int(getattr(before, "st_blocks", 0)) * 512
        if before.st_size >= 4096 and allocated < before.st_size:
            raise RegisteredWorkspaceError("workspace_sparse_file_forbidden")
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(root)
            relative = resolved.relative_to(target)
        except ValueError as exc:
            raise RegisteredWorkspaceError("workspace_path_escape") from exc
        content_digest = self._inspection.hash_file(path, before=before)
        after = self._inspection.lstat(path)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RegisteredWorkspaceError("workspace_path_race")
        return WorkspaceFileManifestEntry(
            relative_path=relative.as_posix(),
            byte_size=before.st_size,
            content_digest=content_digest,
            file_type=_file_type(relative),
        )


def _resolve_registered_path(
    workspace: RegisteredWorkspace,
    relative_path: str,
) -> tuple[Path, Path, str]:
    raw = str(relative_path or ".").strip()
    if "\x00" in raw or "\\" in raw:
        raise RegisteredWorkspaceError("workspace_relative_path_invalid")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"", ".."} for part in pure.parts):
        raise RegisteredWorkspaceError("workspace_relative_path_invalid")
    normalized_parts = tuple(part for part in pure.parts if part != ".")
    normalized = "/".join(normalized_parts) or "."
    if workspace.root.is_symlink():
        raise RegisteredWorkspaceError("workspace_root_symlink_forbidden")
    root = workspace.root.resolve(strict=True)
    target = root.joinpath(*normalized_parts)
    cursor = root
    for part in normalized_parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RegisteredWorkspaceError("workspace_symlink_forbidden")
    resolved_target = target.resolve(strict=True)
    try:
        resolved_target.relative_to(root)
    except ValueError as exc:
        raise RegisteredWorkspaceError("workspace_path_escape") from exc
    return root, resolved_target, normalized


def _hash_file_no_follow(path: Path, *, before: os.stat_result) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RegisteredWorkspaceError("workspace_path_race")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            _read_bounded(stream, digest)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _read_bounded(stream: BinaryIO, digest: Any) -> None:
    while True:
        chunk = stream.read(128 * 1024)
        if not chunk:
            return
        digest.update(chunk)


def _file_type(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix if re.fullmatch(r"[a-z0-9+_-]{1,32}", suffix) else "unknown"


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "DelegatedWorkspaceReference",
    "PosixWorkspaceFileInspection",
    "RegisteredWorkspace",
    "RegisteredWorkspaceCatalogPort",
    "RegisteredWorkspaceConnector",
    "RegisteredWorkspaceError",
    "WorkspaceFileInspectionPort",
    "WorkspaceFileManifestEntry",
    "WorkspaceInventoryLimits",
    "WorkspaceInventoryManifest",
]
