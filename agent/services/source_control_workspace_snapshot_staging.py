"""Stage untrusted browser-folder uploads into a verified snapshot manifest."""

from __future__ import annotations

from collections.abc import Iterable
from typing import BinaryIO, Protocol

from agent.services.source_control_workspace_snapshot_contracts import (
    BrowserSnapshotRelativePath,
    StagedSnapshotFile,
    StagedSnapshotManifest,
    WorkspaceSnapshotContractError,
    WorkspaceSnapshotLimits,
    WorkspaceSnapshotUploadFile,
)
from agent.services.source_control_workspace_snapshot_digests import (
    canonical_digest,
    snapshot_file_type,
)
from agent.services.source_control_workspace_snapshot_errors import (
    WorkspaceSnapshotUploadError,
)


class SnapshotFileWriter(Protocol):
    """The one filesystem capability the stager needs."""

    def write_file(
        self,
        *,
        stage_fd: int,
        relative: BrowserSnapshotRelativePath,
        stream: BinaryIO,
        total_before: int,
        limits: WorkspaceSnapshotLimits,
    ) -> tuple[int, str]: ...


class WorkspaceSnapshotStager:
    """Validate paths, reject collisions and enforce the upload budgets."""

    def __init__(
        self,
        *,
        writer: SnapshotFileWriter,
        limits: WorkspaceSnapshotLimits,
    ) -> None:
        self._writer = writer
        self._limits = limits

    def stage(
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
            byte_size, content_digest = self._writer.write_file(
                stage_fd=stage_fd,
                relative=relative,
                stream=stream,
                total_before=total_bytes,
                limits=self._limits,
            )
            total_bytes += byte_size
            records.append(
                StagedSnapshotFile(
                    relative_path=relative.relative_path,
                    byte_size=byte_size,
                    content_digest=content_digest,
                    file_type=snapshot_file_type(relative.relative_path),
                )
            )
            seen_files.add(relative.collision_key)
            seen_directories.update(directory_keys)
        if not records:
            raise WorkspaceSnapshotUploadError(
                "workspace_snapshot_files_required"
            )
        records.sort(key=lambda item: item.relative_path)
        manifest_digest = canonical_digest(
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


__all__ = [
    "SnapshotFileWriter",
    "WorkspaceSnapshotStager",
]
