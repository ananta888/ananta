"""Re-scan a materialized snapshot and prove it equals the staged manifest."""

from __future__ import annotations

from pathlib import Path

from agent.services.source_admission_service import SourceAdmissionBudgets
from agent.services.source_control_workspace_snapshot_contracts import (
    StagedSnapshotManifest,
    WorkspaceSnapshotLimits,
)
from agent.services.source_control_workspace_snapshot_digests import (
    canonical_digest,
)
from agent.services.source_control_workspace_snapshot_errors import (
    WorkspaceSnapshotUploadError,
)
from agent.services.source_filesystem_scanner import (
    ProductionFilesystemSourceScanner,
)
from agent.sources.git_source_connector_common import GitSourceScope
from agent.sources.registered_workspace_connector import (
    RegisteredWorkspace,
    WorkspaceFileManifestEntry,
    WorkspaceInventoryManifest,
)


class WorkspaceSnapshotRevalidator:
    """Fail closed unless the production scanner reproduces the manifest."""

    def __init__(
        self,
        *,
        scanner: ProductionFilesystemSourceScanner,
        limits: WorkspaceSnapshotLimits,
    ) -> None:
        self._scanner = scanner
        self._limits = limits

    def revalidate(
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
            revision_digest=canonical_digest(
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


__all__ = ["WorkspaceSnapshotRevalidator"]
