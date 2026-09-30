"""Atomic browser-folder materialization and workspace registration service.

``WorkspaceSnapshotUploadService`` sequences one fail-closed upload
transaction: authorize, stage, revalidate, claim idempotency, publish,
register and audit. The work itself is delegated to focused collaborators
(SRP), each a keyword-only constructor seam with a production default:

* ``WorkspaceSnapshotFilesystem`` -- descriptor-relative locking, staging,
  streaming writes, publication and cleanup;
* ``WorkspaceSnapshotStager`` -- path validation, collision and budget checks;
* ``WorkspaceSnapshotRevalidator`` -- scanner-based manifest re-verification;
* ``source_control_workspace_snapshot_digests`` -- pure keys and digests.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from agent.common.audit import log_audit
from agent.services.project_access_authority import (
    ProjectAccessError,
    ProjectAccessPort,
    ProjectCapability,
)
from agent.services.source_control_workspace_catalog import (
    SourceControlWorkspaceCatalogError,
)
from agent.services.source_control_workspace_registration_service import (
    SourceControlWorkspaceRegistrationError,
)
from agent.services.source_control_workspace_snapshot_contracts import (
    BrowserFolderSnapshotRequest,
    StagedSnapshotManifest,
    WorkspaceSnapshotContractError,
    WorkspaceSnapshotLimits,
    WorkspaceSnapshotResult,
    WorkspaceSnapshotUploadFile,
)
from agent.services.source_control_workspace_snapshot_digests import (
    provisional_workspace_id,
    published_snapshot_name,
    snapshot_operation_key,
    snapshot_plan_digest,
)
from agent.services.source_control_workspace_snapshot_errors import (
    WorkspaceSnapshotUploadError,
)
from agent.services.source_control_workspace_snapshot_filesystem import (
    WorkspaceSnapshotFilesystem,
)
from agent.services.source_control_workspace_snapshot_revalidation import (
    WorkspaceSnapshotRevalidator,
)
from agent.services.source_control_workspace_snapshot_staging import (
    WorkspaceSnapshotStager,
)
from agent.services.source_filesystem_scanner import (
    ProductionFilesystemSourceScanner,
    SourceFilesystemScanError,
)
from agent.sources.git_source_connector_common import GitSourceScope


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,190}$")


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
        filesystem: WorkspaceSnapshotFilesystem | None = None,
        stager: WorkspaceSnapshotStager | None = None,
        revalidator: WorkspaceSnapshotRevalidator | None = None,
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
        self._limits = limits or WorkspaceSnapshotLimits()
        self._audit_sink = audit_sink
        self._filesystem = filesystem or WorkspaceSnapshotFilesystem(
            token_factory=token_factory
        )
        self._stager = stager or WorkspaceSnapshotStager(
            writer=self._filesystem,
            limits=self._limits,
        )
        self._revalidator = revalidator or WorkspaceSnapshotRevalidator(
            scanner=scanner or ProductionFilesystemSourceScanner(),
            limits=self._limits,
        )

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
        operation_key = snapshot_operation_key(
            scope=scope,
            idempotency_key=request_contract.idempotency_key,
        )
        final_name = published_snapshot_name(
            display_name=request_contract.display_name,
            operation_key=operation_key,
        )
        manifest: StagedSnapshotManifest | None = None
        workspace_id: str | None = None
        try:
            with self._filesystem.locked_project_root(
                workspace_root=self._workspace_root,
                scope=scope,
            ) as locked:
                project_root, project_fd = locked
                stage_name, stage_fd = self._filesystem.create_staging(
                    project_fd
                )
                stage_root = project_root / stage_name
                published_here = False
                registered = False
                stage_ready = False
                try:
                    manifest = self._stager.stage(
                        stage_fd=stage_fd,
                        uploads=files,
                    )
                    os.fsync(stage_fd)
                    stage_ready = True
                finally:
                    os.close(stage_fd)
                    if not stage_ready:
                        self._filesystem.remove_tree(stage_root)
                        self._filesystem.fsync_quiet(project_fd)
                try:
                    self._revalidator.revalidate(
                        root=stage_root,
                        scope=scope,
                        manifest=manifest,
                        workspace_id=provisional_workspace_id(
                            operation_key
                        ),
                    )
                    plan_digest = snapshot_plan_digest(
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
                    if self._filesystem.published_exists(
                        project_fd=project_fd,
                        stage_name=stage_name,
                        final_name=final_name,
                    ):
                        self._revalidator.revalidate(
                            root=project_root / final_name,
                            scope=scope,
                            manifest=manifest,
                            workspace_id=provisional_workspace_id(
                                operation_key
                            ),
                        )
                    else:
                        self._filesystem.publish_staging(
                            project_fd=project_fd,
                            stage_name=stage_name,
                            final_name=final_name,
                        )
                        published_here = True
                        os.fsync(project_fd)
                        self._revalidator.revalidate(
                            root=project_root / final_name,
                            scope=scope,
                            manifest=manifest,
                            workspace_id=provisional_workspace_id(
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
                        self._filesystem.remove_tree(project_root / final_name)
                        self._filesystem.fsync_quiet(project_fd)
                    raise
                finally:
                    self._filesystem.remove_tree(stage_root)
                    self._filesystem.fsync_quiet(project_fd)
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


__all__ = [
    "WorkspaceSnapshotFilesystem",
    "WorkspaceSnapshotRevalidator",
    "WorkspaceSnapshotStager",
    "WorkspaceSnapshotUploadError",
    "WorkspaceSnapshotUploadService",
]
