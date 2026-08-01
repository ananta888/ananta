"""Atomic browser-folder materialization and workspace registration service."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

import fcntl

from agent.common.audit import log_audit
from agent.services.project_access_authority import (
    ProjectAccessError,
    ProjectAccessPort,
    ProjectCapability,
)
from agent.services.source_admission_service import SourceAdmissionBudgets
from agent.services.source_control_workspace_catalog import (
    SourceControlWorkspaceCatalogError,
)
from agent.services.source_control_workspace_registration_service import (
    SourceControlWorkspaceRegistrationError,
)
from agent.services.source_control_workspace_snapshot_contracts import (
    BrowserFolderSnapshotRequest,
    BrowserSnapshotRelativePath,
    StagedSnapshotFile,
    StagedSnapshotManifest,
    WorkspaceSnapshotContractError,
    WorkspaceSnapshotLimits,
    WorkspaceSnapshotResult,
    WorkspaceSnapshotUploadFile,
)
from agent.services.source_filesystem_scanner import (
    ProductionFilesystemSourceScanner,
    SourceFilesystemScanError,
)
from agent.sources.git_source_connector_common import GitSourceScope
from agent.sources.registered_workspace_connector import (
    RegisteredWorkspace,
    WorkspaceFileManifestEntry,
    WorkspaceInventoryManifest,
)


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
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,190}$")
_FILE_TYPE = re.compile(r"[a-z0-9+_-]{1,32}")
_STAGING_PREFIX = ".ananta-snapshot-"
_LOCK_NAME = ".ananta-workspace-snapshot.lock"
_CHUNK_BYTES = 128 * 1024


class WorkspaceSnapshotUploadError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class WorkspaceSnapshotUploadService:
    """Own one fail-closed upload transaction; dependencies remain ports."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None,
        project_access: ProjectAccessPort,
        folders: object,
        workspace_registrations: object,
        idempotency: object,
        scanner: ProductionFilesystemSourceScanner | None = None,
        limits: WorkspaceSnapshotLimits | None = None,
        audit_sink: Callable[[str, dict], None] = log_audit,
        token_factory: Callable[[], str] = (
            lambda: secrets.token_urlsafe(32)
        ),
    ) -> None:
        self._workspace_root = (
            Path(workspace_root).expanduser()
            if isinstance(workspace_root, (str, Path))
            and str(workspace_root).strip()
            else None
        )
        self._project_access = project_access
        self._folders = folders
        self._registrations = workspace_registrations
        self._idempotency = idempotency
        self._scanner = scanner or ProductionFilesystemSourceScanner()
        self._limits = limits or WorkspaceSnapshotLimits()
        self._audit_sink = audit_sink
        self._token_factory = token_factory

    @property
    def limits(self) -> WorkspaceSnapshotLimits:
        return self._limits

    def upload(
        self,
        *,
        principal: object,
        display_name: object,
        files: Iterable[WorkspaceSnapshotUploadFile],
        idempotency_key: object,
    ) -> Mapping[str, object]:
        request_contract = self._request(
            display_name=display_name,
            idempotency_key=idempotency_key,
        )
        scope = self._authorized_scope(principal)
        operation_key = self._operation_key(
            scope=scope,
            idempotency_key=request_contract.idempotency_key,
        )
        final_name = self._published_name(
            display_name=request_contract.display_name,
            operation_key=operation_key,
        )
        manifest: StagedSnapshotManifest | None = None
        workspace_id: str | None = None
        try:
            with self._locked_project_root(scope=scope) as locked:
                project_root, project_fd = locked
                stage_name, stage_fd = self._create_staging(project_fd)
                stage_root = project_root / stage_name
                published_here = False
                registered = False
                stage_ready = False
                try:
                    manifest = self._stage(
                        stage_fd=stage_fd,
                        uploads=files,
                    )
                    os.fsync(stage_fd)
                    stage_ready = True
                finally:
                    os.close(stage_fd)
                    if not stage_ready:
                        self._remove_tree(stage_root)
                        self._fsync_quiet(project_fd)
                try:
                    self._revalidate(
                        root=stage_root,
                        scope=scope,
                        manifest=manifest,
                        workspace_id=self._provisional_workspace_id(
                            operation_key
                        ),
                    )
                    plan_digest = self._plan_digest(
                        scope=scope,
                        request=request_contract,
                        manifest=manifest,
                    )
                    claim = self._claim(
                        operation_key=operation_key,
                        plan_digest=plan_digest,
                    )
                    if getattr(claim, "state", None) == "completed":
                        replay = self._replayed_result(claim)
                        self._audit(
                            scope=scope,
                            decision="allow",
                            reason_code="workspace_snapshot_replayed",
                            manifest=manifest,
                            workspace_id=replay.workspace_id,
                            replayed=True,
                        )
                        return replay.to_public()
                    claim_token = self._claim_token(claim)
                    if self._published_exists(
                        project_fd=project_fd,
                        stage_name=stage_name,
                        final_name=final_name,
                    ):
                        self._revalidate(
                            root=project_root / final_name,
                            scope=scope,
                            manifest=manifest,
                            workspace_id=self._provisional_workspace_id(
                                operation_key
                            ),
                        )
                    else:
                        os.rename(
                            stage_name,
                            final_name,
                            src_dir_fd=project_fd,
                            dst_dir_fd=project_fd,
                        )
                        published_here = True
                        os.fsync(project_fd)
                        self._revalidate(
                            root=project_root / final_name,
                            scope=scope,
                            manifest=manifest,
                            workspace_id=self._provisional_workspace_id(
                                operation_key
                            ),
                        )
                    created = self._register_workspace(
                        principal=principal,
                        scope=scope,
                        project_root=project_root,
                        final_name=final_name,
                        claim_token=claim_token,
                    )
                    workspace_id = str(created.get("workspace_id") or "")
                    try:
                        result = WorkspaceSnapshotResult(
                            workspace_id=workspace_id,
                            state=str(created.get("state") or "active"),
                            file_count=manifest.file_count,
                            total_bytes=manifest.total_bytes,
                            replayed=False,
                        )
                    except WorkspaceSnapshotContractError as exc:
                        raise WorkspaceSnapshotUploadError(
                            exc.reason_code,
                            status_code=exc.status_code,
                        ) from None
                    registered = True
                    self._complete(
                        operation_key=operation_key,
                        plan_digest=plan_digest,
                        claim_token=claim_token,
                        result=result,
                    )
                    self._audit(
                        scope=scope,
                        decision="allow",
                        reason_code="workspace_snapshot_registered",
                        manifest=manifest,
                        workspace_id=result.workspace_id,
                        replayed=False,
                    )
                    return result.to_public()
                except Exception:
                    if published_here and not registered:
                        self._remove_tree(project_root / final_name)
                        self._fsync_quiet(project_fd)
                    raise
                finally:
                    self._remove_tree(stage_root)
                    self._fsync_quiet(project_fd)
        except WorkspaceSnapshotUploadError as exc:
            self._audit(
                scope=scope,
                decision="deny",
                reason_code=exc.reason_code,
                manifest=manifest,
                workspace_id=workspace_id,
                replayed=False,
            )
            raise
        except (
            SourceControlWorkspaceCatalogError,
            SourceControlWorkspaceRegistrationError,
            SourceFilesystemScanError,
        ) as exc:
            translated = WorkspaceSnapshotUploadError(
                str(getattr(exc, "reason_code", "") or "workspace_snapshot_failed"),
                status_code=int(getattr(exc, "status_code", 409)),
            )
            self._audit(
                scope=scope,
                decision="deny",
                reason_code=translated.reason_code,
                manifest=manifest,
                workspace_id=workspace_id,
                replayed=False,
            )
            raise translated from None

    @staticmethod
    def _request(
        *,
        display_name: object,
        idempotency_key: object,
    ) -> BrowserFolderSnapshotRequest:
        try:
            return BrowserFolderSnapshotRequest.from_values(
                display_name=display_name,
                idempotency_key=idempotency_key,
            )
        except WorkspaceSnapshotContractError as exc:
            raise WorkspaceSnapshotUploadError(
                exc.reason_code,
                status_code=exc.status_code,
            ) from None

    def _authorized_scope(self, principal: object) -> GitSourceScope:
        roles = frozenset(getattr(principal, "roles", frozenset()) or ())
        tenant_id = str(getattr(principal, "tenant_id", "") or "").strip()
        project_id = str(getattr(principal, "project_id", "") or "").strip()
        subject_id = str(getattr(principal, "subject_id", "") or "").strip()
        if not all(
            _OPAQUE_ID.fullmatch(value)
            for value in (tenant_id, project_id, subject_id)
        ):
            raise WorkspaceSnapshotUploadError(
                "source_control_principal_scope_required",
                status_code=403,
            )
        try:
            authorized = self._project_access.require(
                tenant_id=tenant_id,
                project_id=project_id,
                subject_id=subject_id,
                capability=ProjectCapability.WRITE,
                tenant_admin="admin" in roles,
            )
        except ProjectAccessError as exc:
            raise WorkspaceSnapshotUploadError(
                exc.reason_code,
                status_code=exc.public_status,
            ) from None
        return GitSourceScope(
            tenant_id=authorized.tenant_id,
            project_id=authorized.project_id,
            owner_id=authorized.subject_id,
        )

    @contextmanager
    def _locked_project_root(
        self,
        *,
        scope: GitSourceScope,
    ) -> Iterator[tuple[Path, int]]:
        base = self._resolved_workspace_root()
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
            tenant_fd = self._ensure_directory(base_fd, tenant_name)
            project_fd = self._ensure_directory(tenant_fd, project_name)
            lock_fd = os.open(
                _LOCK_NAME,
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

    def _resolved_workspace_root(self) -> Path:
        configured = self._workspace_root
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
    def _ensure_directory(parent_fd: int, name: str) -> int:
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

    def _create_staging(self, project_fd: int) -> tuple[str, int]:
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

    def _stage(
        self,
        *,
        stage_fd: int,
        uploads: Iterable[WorkspaceSnapshotUploadFile],
    ) -> StagedSnapshotManifest:
        records: list[StagedSnapshotFile] = []
        browser_root: str | None = None
        seen_files: set[str] = set()
        seen_directories: set[str] = set()
        total_bytes = 0
        for upload in uploads:
            if len(records) >= self._limits.max_files:
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_file_count_exceeded",
                    status_code=413,
                )
            try:
                relative = BrowserSnapshotRelativePath.parse(
                    getattr(upload, "filename", None),
                    limits=self._limits,
                )
            except WorkspaceSnapshotContractError as exc:
                raise WorkspaceSnapshotUploadError(
                    exc.reason_code,
                    status_code=exc.status_code,
                ) from None
            if browser_root is None:
                browser_root = relative.browser_root
            elif relative.browser_root != browser_root:
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_browser_root_mismatch"
                )
            directory_keys = tuple(
                "/".join(
                    part.casefold() for part in relative.parts[:index]
                )
                for index in range(1, len(relative.parts))
            )
            if (
                relative.collision_key in seen_files
                or relative.collision_key in seen_directories
                or any(key in seen_files for key in directory_keys)
            ):
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_case_collision",
                    status_code=409,
                )
            stream = getattr(upload, "stream", None)
            if stream is None or not callable(getattr(stream, "read", None)):
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_file_stream_invalid"
                )
            byte_size, content_digest = self._write_file(
                stage_fd=stage_fd,
                relative=relative,
                stream=stream,
                total_before=total_bytes,
            )
            total_bytes += byte_size
            records.append(
                StagedSnapshotFile(
                    relative_path=relative.relative_path,
                    byte_size=byte_size,
                    content_digest=content_digest,
                    file_type=self._file_type(relative.relative_path),
                )
            )
            seen_files.add(relative.collision_key)
            seen_directories.update(directory_keys)
        if not records:
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_files_required"
            )
        records.sort(key=lambda item: item.relative_path)
        manifest_digest = self._canonical_digest(
            [
                {
                    "relative_path": item.relative_path,
                    "byte_size": item.byte_size,
                    "content_digest": item.content_digest,
                    "file_type": item.file_type,
                }
                for item in records
            ]
        )
        return StagedSnapshotManifest(
            files=tuple(records),
            total_bytes=total_bytes,
            manifest_digest=manifest_digest,
        )

    def _write_file(
        self,
        *,
        stage_fd: int,
        relative: BrowserSnapshotRelativePath,
        stream: BinaryIO,
        total_before: int,
    ) -> tuple[int, str]:
        directory_fds: list[int] = []
        parent_fd = stage_fd
        file_fd: int | None = None
        try:
            for component in relative.parts[:-1]:
                child_fd = self._ensure_directory(parent_fd, component)
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
                if byte_size > self._limits.max_file_bytes:
                    raise WorkspaceSnapshotUploadError(
                        "workspace_snapshot_file_bytes_exceeded",
                        status_code=413,
                    )
                if total_before + byte_size > self._limits.max_total_bytes:
                    raise WorkspaceSnapshotUploadError(
                        "workspace_snapshot_total_bytes_exceeded",
                        status_code=413,
                    )
                digest.update(chunk)
                self._write_all(file_fd, chunk)
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
                self._fsync_quiet(descriptor)
                os.close(descriptor)

    @staticmethod
    def _write_all(descriptor: int, value: bytes) -> None:
        view = memoryview(value)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            offset += written

    def _revalidate(
        self,
        *,
        root: Path,
        scope: GitSourceScope,
        manifest: StagedSnapshotManifest,
        workspace_id: str,
    ) -> None:
        workspace = RegisteredWorkspace(
            workspace_id=workspace_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            owner_id=scope.owner_id,
            root=root,
            enabled=True,
            read_only=True,
        )
        inventory = WorkspaceInventoryManifest(
            workspace_id=workspace_id,
            relative_root=".",
            entries=tuple(
                WorkspaceFileManifestEntry(
                    relative_path=item.relative_path,
                    byte_size=item.byte_size,
                    content_digest=item.content_digest,
                    file_type=item.file_type,
                )
                for item in manifest.files
            ),
            total_bytes=manifest.total_bytes,
            manifest_digest=manifest.manifest_digest,
            revision_digest=self._canonical_digest(
                {
                    "workspace_id": workspace_id,
                    "relative_root": ".",
                    "manifest_digest": manifest.manifest_digest,
                }
            ),
        )
        try:
            result = self._scanner.scan(
                workspace=workspace,
                snapshot=inventory,
                budgets=SourceAdmissionBudgets(
                    max_files=self._limits.max_files,
                    max_file_bytes=self._limits.max_file_bytes,
                    max_total_bytes=self._limits.max_total_bytes,
                ),
            )
        except WorkspaceSnapshotUploadError:
            raise
        except Exception as exc:
            reason_code = str(
                getattr(exc, "reason_code", "")
                or "workspace_snapshot_revalidation_failed"
            )
            raise WorkspaceSnapshotUploadError(
                reason_code,
                status_code=409,
            ) from None
        if (
            result.scan.completed is not True
            or result.scan.scan_error_count != 0
            or result.inventory.manifest_digest != manifest.manifest_digest
            or result.inventory.file_count != manifest.file_count
            or result.inventory.total_bytes != manifest.total_bytes
            or result.inventory.symlink_count != 0
            or result.inventory.hardlink_count != 0
            or result.inventory.sparse_file_count != 0
        ):
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_revalidation_failed",
                status_code=409,
            )

    def _register_workspace(
        self,
        *,
        principal: object,
        scope: GitSourceScope,
        project_root: Path,
        final_name: str,
        claim_token: str,
    ) -> Mapping[str, object]:
        try:
            snapshots = self._folders.list_folders(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
            )
            matches = tuple(
                snapshot
                for snapshot in snapshots
                if snapshot.root.parent == project_root
                and snapshot.root.name == final_name
            )
            if len(matches) != 1:
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_projection_failed",
                    status_code=409,
                )
            validation = self._registrations.validate(
                principal=principal,
                payload={"folder_handle": matches[0].folder_handle},
            )
            validation_handle = str(
                validation.get("validation_handle") or ""
            )
            registration_key = "snapshot-reg:" + hashlib.sha256(
                claim_token.encode("utf-8")
            ).hexdigest()[:48]
            created = self._registrations.create(
                principal=principal,
                payload={"validation_handle": validation_handle},
                idempotency_key=registration_key,
            )
            if not isinstance(created, Mapping):
                raise WorkspaceSnapshotUploadError(
                    "workspace_snapshot_registration_result_invalid",
                    status_code=500,
                )
            return dict(created)
        except WorkspaceSnapshotUploadError:
            raise
        except Exception as exc:
            raise self._translated_dependency_error(
                exc,
                fallback="workspace_snapshot_registration_failed",
            ) from None

    def _claim(
        self,
        *,
        operation_key: str,
        plan_digest: str,
    ) -> object:
        method = getattr(self._idempotency, "claim", None)
        if not callable(method):
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_idempotency_unavailable",
                status_code=503,
            )
        try:
            claim = method(
                idempotency_key=operation_key,
                plan_digest=plan_digest,
            )
        except Exception as exc:
            raise self._translated_dependency_error(
                exc,
                fallback="workspace_snapshot_idempotency_failed",
            ) from None
        state = getattr(claim, "state", None)
        if state == "in_progress":
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_upload_in_progress",
                status_code=409,
            )
        if state not in {"claimed", "completed"}:
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_idempotency_claim_failed",
                status_code=409,
            )
        return claim

    def _complete(
        self,
        *,
        operation_key: str,
        plan_digest: str,
        claim_token: str,
        result: WorkspaceSnapshotResult,
    ) -> None:
        method = getattr(self._idempotency, "complete", None)
        if not callable(method):
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_idempotency_unavailable",
                status_code=503,
            )
        try:
            method(
                idempotency_key=operation_key,
                plan_digest=plan_digest,
                claim_token=claim_token,
                result=result.to_public(),
            )
        except Exception as exc:
            raise self._translated_dependency_error(
                exc,
                fallback="workspace_snapshot_idempotency_completion_failed",
            ) from None

    @staticmethod
    def _translated_dependency_error(
        exc: Exception,
        *,
        fallback: str,
    ) -> WorkspaceSnapshotUploadError:
        reason_code = str(getattr(exc, "reason_code", "") or fallback)
        status_code = int(getattr(exc, "status_code", 409))
        return WorkspaceSnapshotUploadError(
            reason_code,
            status_code=status_code,
        )

    @staticmethod
    def _claim_token(claim: object) -> str:
        token = getattr(claim, "claim_token", None)
        if (
            getattr(claim, "state", None) != "claimed"
            or not isinstance(token, str)
            or not token
        ):
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_idempotency_claim_failed",
                status_code=409,
            )
        return token

    @staticmethod
    def _replayed_result(claim: object) -> WorkspaceSnapshotResult:
        payload = getattr(claim, "result", None)
        if not isinstance(payload, Mapping):
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_idempotency_result_invalid",
                status_code=500,
            )
        try:
            return WorkspaceSnapshotResult.from_mapping(
                payload,
                replayed=True,
            )
        except WorkspaceSnapshotContractError as exc:
            raise WorkspaceSnapshotUploadError(
                exc.reason_code,
                status_code=exc.status_code,
            ) from None

    @staticmethod
    def _operation_key(
        *,
        scope: GitSourceScope,
        idempotency_key: str,
    ) -> str:
        digest = hashlib.sha256(
            (
                f"workspace-snapshot-v1\0{scope.tenant_id}\0"
                f"{scope.project_id}\0{scope.owner_id}\0{idempotency_key}"
            ).encode("utf-8")
        ).hexdigest()
        return f"workspace-snapshot:{digest}"

    @staticmethod
    def _published_name(*, display_name: str, operation_key: str) -> str:
        suffix = hashlib.sha256(operation_key.encode("ascii")).hexdigest()[:16]
        return f"{display_name}--{suffix}"

    @staticmethod
    def _provisional_workspace_id(operation_key: str) -> str:
        token = hashlib.sha256(operation_key.encode("ascii")).hexdigest()
        return f"ws_{token}"

    def _published_exists(
        self,
        *,
        project_fd: int,
        stage_name: str,
        final_name: str,
    ) -> bool:
        target_key = final_name.casefold()
        exact = False
        for name in os.listdir(project_fd):
            if name in {stage_name, _LOCK_NAME}:
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
    def _plan_digest(
        *,
        scope: GitSourceScope,
        request: BrowserFolderSnapshotRequest,
        manifest: StagedSnapshotManifest,
    ) -> str:
        return WorkspaceSnapshotUploadService._canonical_digest(
            {
                "operation": "workspace_snapshot_upload",
                "scope": {
                    "tenant_id": scope.tenant_id,
                    "project_id": scope.project_id,
                    "owner_id": scope.owner_id,
                },
                "display_name": request.display_name,
                "manifest_digest": manifest.manifest_digest,
                "file_count": manifest.file_count,
                "total_bytes": manifest.total_bytes,
            }
        )

    @staticmethod
    def _canonical_digest(payload: object) -> str:
        return hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _file_type(relative_path: str) -> str:
        suffix = Path(relative_path).suffix.lower().lstrip(".")
        return suffix if _FILE_TYPE.fullmatch(suffix) else "unknown"

    def _audit(
        self,
        *,
        scope: GitSourceScope,
        decision: str,
        reason_code: str,
        manifest: StagedSnapshotManifest | None,
        workspace_id: str | None,
        replayed: bool,
    ) -> None:
        safe_reason = (
            reason_code
            if re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,127}", reason_code)
            else "workspace_snapshot_failed"
        )
        event = {
            "tenant_id": scope.tenant_id,
            "project_id": scope.project_id,
            "actor_id": scope.owner_id,
            "workspace_id_digest": (
                hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()
                if workspace_id
                else None
            ),
            "decision": decision,
            "reason_code": safe_reason,
            "file_count": manifest.file_count if manifest else 0,
            "total_bytes": manifest.total_bytes if manifest else 0,
            "replayed": replayed,
        }
        try:
            self._audit_sink(
                "source_control_workspace_snapshot_upload",
                event,
            )
        except Exception:
            pass

    @staticmethod
    def _remove_tree(path: Path) -> None:
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
    def _fsync_quiet(descriptor: int) -> None:
        try:
            os.fsync(descriptor)
        except OSError:
            pass


__all__ = [
    "WorkspaceSnapshotUploadError",
    "WorkspaceSnapshotUploadService",
]
