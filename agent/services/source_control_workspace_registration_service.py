"""Hub application service for path-free workspace registration."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable, Mapping

from agent.repositories.source_control_workspace_registration_repository import (
    SourceControlWorkspacePersistenceError,
)
from agent.services.source_control_workspace_catalog import (
    SourceControlWorkspaceCatalogError,
)
from agent.services.source_control_workspace_contracts import (
    SourceControlWorkspaceContractError,
    WorkspaceCreateSelection,
    WorkspaceFolderSelection,
    WorkspaceRegistrationRecord,
    WorkspaceValidationBinding,
)
from agent.sources.git_source_connector_common import GitSourceScope

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,190}$")
_WORKSPACE_ID = re.compile(r"^ws_[A-Za-z0-9_-]{32,96}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


class SourceControlWorkspaceRegistrationError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class SourceControlWorkspaceRegistrationService:
    def __init__(
        self,
        *,
        repository: object,
        folders: object,
        idempotency: object,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = (
            lambda: secrets.token_urlsafe(32)
        ),
    ) -> None:
        if not 30 <= int(ttl_seconds) <= 900:
            raise ValueError("workspace_validation_ttl_invalid")
        self._repository = repository
        self._folders = folders
        self._idempotency = idempotency
        self._ttl_seconds = int(ttl_seconds)
        self._clock = clock
        self._token_factory = token_factory

    def list_folders(self, *, principal: object) -> Mapping[str, object]:
        scope, _admin = self._principal(principal)
        folders = self._folders.list_folders(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
        )
        return {
            "items": [
                {
                    "folder_handle": item.folder_handle,
                    "display_name": item.display_name,
                    "capabilities": {
                        "selection_only": True,
                        "read_only": True,
                        "path_exposed": False,
                        "file_names_exposed": False,
                        "folder_label_exposed": True,
                    },
                }
                for item in folders
            ],
            "capabilities": {
                "project_scoped": True,
                "raw_paths_exposed": False,
            },
        }

    def validate(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        scope, _admin = self._principal(principal)
        selection = self._folder_selection(payload)
        snapshot = self._folders.resolve_folder(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            folder_handle=selection.folder_handle,
        )
        if snapshot is None:
            self._record_denial(
                scope=scope,
                workspace_id=selection.folder_handle,
                event_type="validate",
                reason_code="workspace_folder_not_found",
            )
            raise SourceControlWorkspaceRegistrationError(
                "workspace_folder_not_found",
                status_code=404,
            )
        binding = WorkspaceValidationBinding(
            scope=scope,
            folder_handle=snapshot.folder_handle,
            root_fingerprint=snapshot.root_fingerprint,
            manifest_digest=snapshot.manifest_digest,
        )
        expires_at = float(self._clock()) + self._ttl_seconds
        for _attempt in range(3):
            handle = f"wsv1_{self._token_factory()}"
            digest = hashlib.sha256(handle.encode("ascii")).hexdigest()
            try:
                self._repository.store_validation(
                    handle_digest=digest,
                    binding=binding,
                    expires_at_epoch=expires_at,
                )
                return {
                    "validation_handle": handle,
                    "expires_at_epoch": int(expires_at),
                    "capabilities": {
                        "read_only": True,
                        "one_time": True,
                        "path_exposed": False,
                        "filename_exposed": False,
                    },
                }
            except SourceControlWorkspacePersistenceError as exc:
                if (
                    exc.reason_code
                    == "workspace_validation_handle_collision"
                ):
                    continue
                raise
        raise SourceControlWorkspaceRegistrationError(
            "workspace_validation_handle_generation_failed",
            status_code=503,
        )

    def create(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        scope, _admin = self._principal(principal)
        selection = self._create_selection(payload)
        operation_key, plan_digest, claim = self._claim(
            scope=scope,
            idempotency_key=idempotency_key,
            operation="workspace_create",
            coordinates={"handle_digest": selection.handle_digest},
        )
        if getattr(claim, "state", None) == "completed":
            record = self._repository.get_by_validation(
                handle_digest=selection.handle_digest,
                scope=scope,
            )
            return self._view(record)
        claim_token = self._claimed_token(claim)
        try:
            binding = self._repository.validation_binding(
                handle_digest=selection.handle_digest,
                scope=scope,
            )
            snapshot = self._folders.resolve_folder(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                folder_handle=binding.folder_handle,
            )
            if snapshot is None:
                raise SourceControlWorkspaceRegistrationError(
                    "workspace_folder_not_found",
                    status_code=404,
                )
            record = self._repository.consume_validation(
                handle_digest=selection.handle_digest,
                scope=scope,
                snapshot=snapshot,
                workspace_id=f"ws_{self._token_factory()}",
            )
        except (
            SourceControlWorkspaceCatalogError,
            SourceControlWorkspacePersistenceError,
            SourceControlWorkspaceRegistrationError,
        ) as exc:
            self._record_denial(
                scope=scope,
                workspace_id=selection.handle_digest,
                event_type="create",
                reason_code=exc.reason_code,
            )
            raise
        result = self._view(record)
        self._idempotency.complete(
            idempotency_key=operation_key,
            plan_digest=plan_digest,
            claim_token=claim_token,
            result=result,
        )
        return result

    def detail(
        self,
        *,
        principal: object,
        workspace_id: str,
    ) -> Mapping[str, object]:
        scope, admin = self._principal(principal)
        self._require_workspace_id(workspace_id)
        record = self._repository.get_registration(
            workspace_id=workspace_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            owner_id=None if admin else scope.owner_id,
        )
        if record is None:
            raise SourceControlWorkspaceRegistrationError(
                "workspace_registration_not_found",
                status_code=404,
            )
        return self._view(record)

    def disable(
        self,
        *,
        principal: object,
        workspace_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        scope, admin = self._principal(principal)
        self._require_workspace_id(workspace_id)
        operation_key, plan_digest, claim = self._claim(
            scope=scope,
            idempotency_key=idempotency_key,
            operation="workspace_disable",
            coordinates={
                "workspace_id": workspace_id,
                "expected_revision": expected_revision,
            },
        )
        if getattr(claim, "state", None) == "completed":
            result = getattr(claim, "result", None)
            if isinstance(result, Mapping):
                return dict(result)
            raise SourceControlWorkspaceRegistrationError(
                "workspace_idempotency_result_invalid",
                status_code=409,
            )
        claim_token = self._claimed_token(claim)
        try:
            record = self._repository.disable(
                workspace_id=workspace_id,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                owner_id=None if admin else scope.owner_id,
                actor_id=scope.owner_id,
                expected_revision=expected_revision,
            )
        except SourceControlWorkspacePersistenceError as exc:
            self._record_denial(
                scope=scope,
                workspace_id=workspace_id,
                event_type="disable",
                reason_code=exc.reason_code,
            )
            raise
        result = self._view(record)
        self._idempotency.complete(
            idempotency_key=operation_key,
            plan_digest=plan_digest,
            claim_token=claim_token,
            result=result,
        )
        return result

    def _claim(
        self,
        *,
        scope: GitSourceScope,
        idempotency_key: str,
        operation: str,
        coordinates: Mapping[str, object],
    ):
        key = str(idempotency_key or "").strip()
        if _IDEMPOTENCY.fullmatch(key) is None:
            raise SourceControlWorkspaceRegistrationError(
                "idempotency_key_required"
            )
        operation_key = "workspace:" + hashlib.sha256(
            (
                f"{scope.tenant_id}\0{scope.project_id}\0"
                f"{scope.owner_id}\0{key}"
            ).encode("utf-8")
        ).hexdigest()
        plan_digest = hashlib.sha256(
            json.dumps(
                {
                    "operation": operation,
                    "scope": {
                        "tenant_id": scope.tenant_id,
                        "project_id": scope.project_id,
                        "owner_id": scope.owner_id,
                    },
                    "coordinates": dict(coordinates),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        claim = self._idempotency.claim(
            idempotency_key=operation_key,
            plan_digest=plan_digest,
        )
        if getattr(claim, "state", None) == "in_progress":
            raise SourceControlWorkspaceRegistrationError(
                "workspace_operation_in_progress",
                status_code=409,
            )
        return operation_key, plan_digest, claim

    @staticmethod
    def _claimed_token(claim: object) -> str:
        token = getattr(claim, "claim_token", None)
        if (
            getattr(claim, "state", None) != "claimed"
            or not isinstance(token, str)
            or not token
        ):
            raise SourceControlWorkspaceRegistrationError(
                "workspace_idempotency_claim_failed",
                status_code=409,
            )
        return token

    @staticmethod
    def _folder_selection(
        payload: Mapping[str, object],
    ) -> WorkspaceFolderSelection:
        try:
            return WorkspaceFolderSelection.from_mapping(payload)
        except SourceControlWorkspaceContractError as exc:
            raise SourceControlWorkspaceRegistrationError(
                exc.reason_code,
                status_code=exc.status_code,
            ) from None

    @staticmethod
    def _create_selection(
        payload: Mapping[str, object],
    ) -> WorkspaceCreateSelection:
        try:
            return WorkspaceCreateSelection.from_mapping(payload)
        except SourceControlWorkspaceContractError as exc:
            raise SourceControlWorkspaceRegistrationError(
                exc.reason_code,
                status_code=exc.status_code,
            ) from None

    @staticmethod
    def _principal(principal: object) -> tuple[GitSourceScope, bool]:
        roles = frozenset(getattr(principal, "roles", frozenset()) or ())
        if roles.isdisjoint({"admin", "project_owner"}):
            raise SourceControlWorkspaceRegistrationError(
                "workspace_registration_role_required",
                status_code=403,
            )
        values = (
            str(getattr(principal, "tenant_id", "") or "").strip(),
            str(getattr(principal, "project_id", "") or "").strip(),
            str(getattr(principal, "subject_id", "") or "").strip(),
        )
        if not all(_ID.fullmatch(value) for value in values):
            raise SourceControlWorkspaceRegistrationError(
                "source_control_principal_scope_required",
                status_code=403,
            )
        return (
            GitSourceScope(
                tenant_id=values[0],
                project_id=values[1],
                owner_id=values[2],
            ),
            "admin" in roles,
        )

    @staticmethod
    def _require_workspace_id(workspace_id: str) -> None:
        if _WORKSPACE_ID.fullmatch(str(workspace_id or "")) is None:
            raise SourceControlWorkspaceRegistrationError(
                "workspace_id_invalid"
            )

    def _record_denial(
        self,
        *,
        scope: GitSourceScope,
        workspace_id: str,
        event_type: str,
        reason_code: str,
    ) -> None:
        try:
            self._repository.record_denial(
                scope=scope,
                workspace_id=workspace_id,
                event_type=event_type,
                reason_code=reason_code,
            )
        except Exception:
            pass

    @staticmethod
    def _view(record: WorkspaceRegistrationRecord) -> Mapping[str, object]:
        return {
            "workspace_id": record.workspace_id,
            "state": record.registration_state,
            "read_only": True,
            "etag": record.etag,
            "capabilities": {
                "selection_only": True,
                "path_exposed": False,
                "filename_exposed": False,
            },
        }


__all__ = [
    "SourceControlWorkspaceRegistrationError",
    "SourceControlWorkspaceRegistrationService",
]
