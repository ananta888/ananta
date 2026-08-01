"""Secure folder discovery and composite registered-workspace resolution."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import unicodedata
from pathlib import Path

from agent.services.source_control_workspace_contracts import (
    WorkspaceFolderSnapshot,
)
from agent.sources.registered_workspace_connector import RegisteredWorkspace

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_INTERNAL_FOLDER_PREFIXES = (".ananta-snapshot-",)


class SourceControlWorkspaceCatalogError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class SecureWorkspaceFolderCatalog:
    """Enumerate only immediate folders under one hashed project scope."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None,
        max_entries: int = 100_000,
        max_depth: int = 64,
    ) -> None:
        self._configured_root = (
            Path(workspace_root).expanduser()
            if isinstance(workspace_root, (str, Path))
            and str(workspace_root).strip()
            else None
        )
        self._max_entries = int(max_entries)
        self._max_depth = int(max_depth)

    def list_folders(
        self,
        *,
        tenant_id: str,
        project_id: str,
    ) -> tuple[WorkspaceFolderSnapshot, ...]:
        if not hasattr(os, "O_NOFOLLOW"):
            raise SourceControlWorkspaceCatalogError(
                "workspace_nofollow_unavailable",
                status_code=503,
            )
        root = self._project_root(
            tenant_id=tenant_id,
            project_id=project_id,
        )
        if not root.exists():
            return ()
        root_fd = self._open_project_root(root)
        try:
            snapshots = []
            for name in sorted(os.listdir(root_fd), key=os.fsencode):
                if name.startswith(_INTERNAL_FOLDER_PREFIXES):
                    continue
                metadata = os.stat(
                    name,
                    dir_fd=root_fd,
                    follow_symlinks=False,
                )
                if not stat.S_ISDIR(metadata.st_mode):
                    continue
                folder_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=root_fd)
                try:
                    opened = os.fstat(folder_fd)
                    if (
                        opened.st_dev != metadata.st_dev
                        or opened.st_ino != metadata.st_ino
                    ):
                        raise SourceControlWorkspaceCatalogError(
                            "workspace_folder_identity_changed",
                            status_code=409,
                        )
                    fingerprint = self._fingerprint(opened)
                    manifest, count = self._manifest(
                        folder_fd,
                        root_device=opened.st_dev,
                    )
                finally:
                    os.close(folder_fd)
                folder_handle = self._folder_handle(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    name=name,
                    metadata=opened,
                )
                snapshots.append(
                    WorkspaceFolderSnapshot(
                        folder_handle=folder_handle,
                        display_name=self._display_name(
                            name,
                            folder_handle=folder_handle,
                        ),
                        root=root / name,
                        root_fingerprint=fingerprint,
                        manifest_digest=manifest,
                        item_count=count,
                    )
                )
            return tuple(
                sorted(
                    snapshots,
                    key=lambda item: item.folder_handle,
                )
            )
        except OSError as exc:
            raise SourceControlWorkspaceCatalogError(
                "workspace_folder_verification_failed",
                status_code=409,
            ) from exc
        finally:
            os.close(root_fd)

    def resolve_folder(
        self,
        *,
        tenant_id: str,
        project_id: str,
        folder_handle: str,
    ) -> WorkspaceFolderSnapshot | None:
        matches = tuple(
            item
            for item in self.list_folders(
                tenant_id=tenant_id,
                project_id=project_id,
            )
            if item.folder_handle == folder_handle
        )
        return matches[0] if len(matches) == 1 else None

    def _project_root(self, *, tenant_id: str, project_id: str) -> Path:
        if self._configured_root is None:
            raise SourceControlWorkspaceCatalogError(
                "workspace_root_unavailable",
                status_code=503,
            )
        configured = self._configured_root
        if configured.is_symlink():
            raise SourceControlWorkspaceCatalogError(
                "workspace_root_invalid",
                status_code=503,
            )
        try:
            base = configured.resolve(strict=True)
        except OSError as exc:
            raise SourceControlWorkspaceCatalogError(
                "workspace_root_unavailable",
                status_code=503,
            ) from exc
        if not base.is_dir():
            raise SourceControlWorkspaceCatalogError(
                "workspace_root_invalid",
                status_code=503,
            )
        tenant = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        project = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
        tenant_root = base / tenant
        project_root = tenant_root / project
        if tenant_root.is_symlink() or project_root.is_symlink():
            raise SourceControlWorkspaceCatalogError(
                "workspace_scope_root_invalid",
                status_code=409,
            )
        return project_root

    @staticmethod
    def _open_project_root(root: Path) -> int:
        base_fd = None
        tenant_fd = None
        try:
            base_fd = os.open(root.parent.parent, _DIRECTORY_FLAGS)
            tenant_fd = os.open(
                root.parent.name,
                _DIRECTORY_FLAGS,
                dir_fd=base_fd,
            )
            return os.open(
                root.name,
                _DIRECTORY_FLAGS,
                dir_fd=tenant_fd,
            )
        except OSError as exc:
            raise SourceControlWorkspaceCatalogError(
                "workspace_scope_root_unavailable",
                status_code=404,
            ) from exc
        finally:
            if tenant_fd is not None:
                os.close(tenant_fd)
            if base_fd is not None:
                os.close(base_fd)

    def _manifest(
        self,
        root_fd: int,
        *,
        root_device: int,
    ) -> tuple[str, int]:
        digest = hashlib.sha256()
        count = 0

        def walk(directory_fd: int, prefix: bytes, depth: int) -> None:
            nonlocal count
            if depth > self._max_depth:
                raise SourceControlWorkspaceCatalogError(
                    "workspace_manifest_depth_exceeded",
                    status_code=409,
                )
            names = sorted(os.listdir(directory_fd), key=os.fsencode)
            for name in names:
                count += 1
                if count > self._max_entries:
                    raise SourceControlWorkspaceCatalogError(
                        "workspace_manifest_entries_exceeded",
                        status_code=409,
                    )
                metadata = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if metadata.st_dev != root_device:
                    raise SourceControlWorkspaceCatalogError(
                        "workspace_cross_device_denied",
                        status_code=409,
                    )
                raw_name = os.fsencode(name)
                relative = prefix + raw_name
                digest.update(relative)
                digest.update(b"\0")
                digest.update(str(metadata.st_mode).encode("ascii"))
                digest.update(b"\0")
                digest.update(str(metadata.st_size).encode("ascii"))
                digest.update(b"\0")
                digest.update(str(metadata.st_mtime_ns).encode("ascii"))
                digest.update(b"\0")
                if stat.S_ISLNK(metadata.st_mode):
                    raise SourceControlWorkspaceCatalogError(
                        "workspace_symlink_denied",
                        status_code=409,
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    child_fd = os.open(
                        name,
                        _DIRECTORY_FLAGS,
                        dir_fd=directory_fd,
                    )
                    try:
                        opened = os.fstat(child_fd)
                        if (
                            opened.st_dev != metadata.st_dev
                            or opened.st_ino != metadata.st_ino
                        ):
                            raise SourceControlWorkspaceCatalogError(
                                "workspace_folder_identity_changed",
                                status_code=409,
                            )
                        walk(
                            child_fd,
                            relative + b"/",
                            depth + 1,
                        )
                    finally:
                        os.close(child_fd)
                elif not stat.S_ISREG(metadata.st_mode):
                    raise SourceControlWorkspaceCatalogError(
                        "workspace_special_file_denied",
                        status_code=409,
                    )

        walk(root_fd, b"", 0)
        return digest.hexdigest(), count

    @staticmethod
    def _fingerprint(metadata: os.stat_result) -> str:
        return hashlib.sha256(
            (
                f"{metadata.st_dev}\0{metadata.st_ino}\0"
                f"{stat.S_IFMT(metadata.st_mode)}"
            ).encode("ascii")
        ).hexdigest()

    @staticmethod
    def _folder_handle(
        *,
        tenant_id: str,
        project_id: str,
        name: str,
        metadata: os.stat_result,
    ) -> str:
        material = hashlib.sha256()
        material.update(b"ananta-workspace-folder-v1\0")
        material.update(tenant_id.encode("utf-8"))
        material.update(b"\0")
        material.update(project_id.encode("utf-8"))
        material.update(b"\0")
        material.update(os.fsencode(name))
        material.update(b"\0")
        material.update(str(metadata.st_dev).encode("ascii"))
        material.update(b"\0")
        material.update(str(metadata.st_ino).encode("ascii"))
        token = base64.urlsafe_b64encode(material.digest()).decode(
            "ascii"
        ).rstrip("=")
        return f"fld_{token}"

    @staticmethod
    def _display_name(name: str, *, folder_handle: str) -> str:
        normalized = " ".join(
            unicodedata.normalize("NFKC", str(name)).split()
        )
        if (
            not normalized
            or "/" in normalized
            or "\\" in normalized
            or any(not character.isprintable() for character in normalized)
        ):
            return f"Workspace {folder_handle[-8:]}"
        return normalized[:80].rstrip() or f"Workspace {folder_handle[-8:]}"


class SQLRegisteredWorkspaceCatalog:
    """Adapt durable registrations to the existing connector catalog port."""

    def __init__(self, *, repository: object, folders: object) -> None:
        self._repository = repository
        self._folders = folders

    def get(
        self,
        *,
        workspace_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str | None = None,
    ) -> RegisteredWorkspace | None:
        method = getattr(self._repository, "get_registration", None)
        record = (
            method(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=owner_id,
            )
            if callable(method)
            else None
        )
        return self._resolved(record)

    def resolve_registered_workspace(self, **kwargs):
        return self.get(**kwargs)

    def list_registered(
        self,
        *,
        tenant_id: str,
        project_id: str,
        owner_id: str | None = None,
    ) -> tuple[RegisteredWorkspace, ...]:
        method = getattr(self._repository, "list_registrations", None)
        records = (
            method(
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=owner_id,
            )
            if callable(method)
            else ()
        )
        return tuple(
            workspace
            for workspace in (self._resolved(item) for item in records)
            if workspace is not None
        )

    def _resolved(self, record: object) -> RegisteredWorkspace | None:
        if (
            record is None
            or getattr(record, "registration_state", None) != "active"
            or getattr(record, "read_only", None) is not True
        ):
            return None
        binding = getattr(record, "binding", None)
        scope = getattr(binding, "scope", None)
        resolve = getattr(self._folders, "resolve_folder", None)
        if not callable(resolve) or scope is None:
            return None
        try:
            folder = resolve(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                folder_handle=binding.folder_handle,
            )
        except SourceControlWorkspaceCatalogError:
            return None
        if (
            folder is None
            or folder.root_fingerprint != binding.root_fingerprint
            or folder.manifest_digest != binding.manifest_digest
        ):
            return None
        return RegisteredWorkspace(
            workspace_id=record.workspace_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            owner_id=scope.owner_id,
            root=folder.root,
            enabled=True,
            read_only=True,
        )


class CompositeRegisteredWorkspaceCatalog:
    def __init__(self, catalogs: tuple[object, ...]) -> None:
        if not catalogs:
            raise ValueError("registered_workspace_catalog_required")
        self._catalogs = catalogs

    def contains(self, catalog: object) -> bool:
        return any(item is catalog for item in self._catalogs)

    def get(self, **kwargs):
        matches = []
        for catalog in self._catalogs:
            method = getattr(catalog, "get", None)
            if not callable(method):
                raise RuntimeError("registered_workspace_catalog_invalid")
            value = method(**kwargs)
            if value is not None:
                matches.append(value)
        return matches[0] if len(matches) == 1 else None

    def resolve_registered_workspace(self, **kwargs):
        return self.get(**kwargs)

    def list_registered(self, **kwargs) -> tuple[RegisteredWorkspace, ...]:
        values = []
        origins: dict[str, set[int]] = {}
        for index, catalog in enumerate(self._catalogs):
            method = getattr(catalog, "list_registered", None)
            if not callable(method):
                raise RuntimeError("registered_workspace_catalog_invalid")
            for item in method(**kwargs):
                origins.setdefault(item.workspace_id, set()).add(index)
                values.append((index, item))
        return tuple(
            item
            for _index, item in sorted(
                values,
                key=lambda value: value[1].workspace_id,
            )
            if len(origins.get(item.workspace_id, set())) == 1
        )


__all__ = [
    "CompositeRegisteredWorkspaceCatalog",
    "SQLRegisteredWorkspaceCatalog",
    "SecureWorkspaceFolderCatalog",
    "SourceControlWorkspaceCatalogError",
]
