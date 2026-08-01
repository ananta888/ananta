"""Resolve browser-safe connection intents from Hub-owned catalogs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from agent.services.source_control_connection_binding import (
    SourceConnectionSelectorBinding,
    implementation_connector_type,
    normalize_workspace_relative_path,
)


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,191}$")
_WORKSPACE_CONNECTORS = frozenset(
    {"registered_workspace", "local_directory"}
)
_REMOTE_CONNECTORS = frozenset({"git", "github"})


class SourceControlConnectionIntentError(ValueError):
    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 400,
    ) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class ResolvedSourceControlConnectionIntent:
    connector_type: str
    connection_identity_digest: str
    display_name: str
    sensitivity: str
    selector_kind: str
    selector_id: str
    implementation_connector_type: str
    relative_path: str | None = None
    repository_identifier: str | None = None

    def binding(
        self,
        *,
        connection_id: str,
        tenant_id: str,
        project_id: str,
        owner_id: str,
    ) -> SourceConnectionSelectorBinding:
        return SourceConnectionSelectorBinding(
            connection_id=connection_id,
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            public_connector_type=self.connector_type,
            implementation_connector_type=(
                self.implementation_connector_type
            ),
            selector_kind=self.selector_kind,
            selector_id=self.selector_id,
            relative_path=self.relative_path,
            repository_identifier=self.repository_identifier,
        )


class SourceControlConnectionIntentResolver:
    """Derive canonical identities without accepting URLs, paths or digests."""

    def __init__(self, *, workspaces: object, remotes: object) -> None:
        self._workspaces = workspaces
        self._remotes = remotes

    def resolve(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
    ) -> ResolvedSourceControlConnectionIntent:
        tenant_id = str(getattr(principal, "tenant_id", "") or "")
        project_id = str(getattr(principal, "project_id", "") or "")
        actor_id = str(getattr(principal, "subject_id", "") or "")
        roles = frozenset(getattr(principal, "roles", frozenset()) or ())
        if not all(
            _OPAQUE_ID.fullmatch(value)
            for value in (tenant_id, project_id, actor_id)
        ):
            raise SourceControlConnectionIntentError(
                "source_control_principal_scope_required",
                status_code=403,
            )
        connector_type = str(payload.get("connector_type") or "")
        display_name = str(payload.get("display_name") or "").strip()
        sensitivity = str(payload.get("sensitivity") or "").strip()
        if not display_name or len(display_name) > 256:
            raise SourceControlConnectionIntentError("display_name_invalid")
        if _OPAQUE_ID.fullmatch(sensitivity) is None:
            raise SourceControlConnectionIntentError("sensitivity_invalid")
        if connector_type in _WORKSPACE_CONNECTORS:
            selector_id = str(payload.get("workspace_id") or "")
            relative_path = normalize_workspace_relative_path(
                payload.get("relative_path")
            )
            identity = self._workspace_identity(
                connector_type=connector_type,
                tenant_id=tenant_id,
                project_id=project_id,
                actor_id=None if "admin" in roles else actor_id,
                workspace_id=selector_id,
                relative_path=relative_path,
            )
            selector_kind = "workspace"
            repository_identifier = None
        elif connector_type in _REMOTE_CONNECTORS:
            selector_id = str(payload.get("remote_id") or "")
            identity = self._remote_identity(
                connector_type=connector_type,
                tenant_id=tenant_id,
                project_id=project_id,
                actor_id=None if "admin" in roles else actor_id,
                remote_id=selector_id,
            )
            selector_kind = "remote"
            relative_path = None
            repository_identifier = (
                str(identity.get("repository") or "") or None
            )
        else:
            raise SourceControlConnectionIntentError(
                "connector_type_not_registered"
            )
        return ResolvedSourceControlConnectionIntent(
            connector_type=connector_type,
            connection_identity_digest=_digest(identity),
            display_name=display_name,
            sensitivity=sensitivity,
            selector_kind=selector_kind,
            selector_id=selector_id,
            implementation_connector_type=implementation_connector_type(
                connector_type
            ),
            relative_path=relative_path,
            repository_identifier=repository_identifier,
        )

    def _workspace_identity(
        self,
        *,
        connector_type: str,
        tenant_id: str,
        project_id: str,
        actor_id: str | None,
        workspace_id: str,
        relative_path: str,
    ) -> Mapping[str, object]:
        if _OPAQUE_ID.fullmatch(workspace_id) is None:
            raise SourceControlConnectionIntentError("workspace_id_invalid")
        resolve = getattr(
            self._workspaces, "resolve_registered_workspace", None
        )
        if callable(resolve):
            workspace = resolve(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=actor_id,
            )
        else:
            get = getattr(self._workspaces, "get", None)
            workspace = (
                get(
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    project_id=project_id,
                )
                if callable(get)
                else None
            )
        if workspace is None:
            raise SourceControlConnectionIntentError(
                "workspace_not_found", status_code=404
            )
        workspace_owner = getattr(workspace, "owner_id", None)
        if (
            str(getattr(workspace, "workspace_id", "")) != workspace_id
            or str(getattr(workspace, "tenant_id", "")) != tenant_id
            or str(getattr(workspace, "project_id", "")) != project_id
            or (
                actor_id is not None
                and workspace_owner is not None
                and str(workspace_owner) != actor_id
            )
        ):
            raise SourceControlConnectionIntentError(
                "workspace_not_found", status_code=404
            )
        if not bool(getattr(workspace, "enabled", False)):
            raise SourceControlConnectionIntentError("workspace_disabled")
        if not bool(getattr(workspace, "read_only", False)):
            raise SourceControlConnectionIntentError(
                "workspace_read_only_required"
            )
        root = getattr(workspace, "root", None)
        try:
            root_identity = (
                str(root.resolve(strict=True)) if root is not None else ""
            )
        except OSError as exc:
            raise SourceControlConnectionIntentError(
                "workspace_registration_invalid"
            ) from exc
        if not root_identity:
            raise SourceControlConnectionIntentError(
                "workspace_registration_invalid"
            )
        return {
            "kind": "registered_workspace",
            "connector_type": connector_type,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "workspace_id": workspace_id,
            "relative_path": relative_path,
            "owner_id": str(workspace_owner or ""),
            "registered_root_digest": hashlib.sha256(
                root_identity.encode("utf-8")
            ).hexdigest(),
            "read_only": True,
        }

    def _remote_identity(
        self,
        *,
        connector_type: str,
        tenant_id: str,
        project_id: str,
        actor_id: str | None,
        remote_id: str,
    ) -> Mapping[str, object]:
        if _OPAQUE_ID.fullmatch(remote_id) is None:
            raise SourceControlConnectionIntentError("remote_id_invalid")
        resolve = getattr(self._remotes, "resolve_registered_remote", None)
        record = (
            resolve(
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=actor_id,
                remote_id=remote_id,
            )
            if callable(resolve)
            else None
        )
        if record is None:
            raise SourceControlConnectionIntentError(
                "registered_remote_not_found", status_code=404
            )
        expected_connector = (
            "github"
            if str(getattr(record, "authorization_kind", "")).startswith(
                "github_"
            )
            else "git"
        )
        if expected_connector != connector_type:
            raise SourceControlConnectionIntentError(
                "registered_remote_connector_mismatch"
            )
        if str(getattr(record, "authorization_state", "")) != "active":
            raise SourceControlConnectionIntentError(
                "registered_remote_not_active"
            )
        scope = getattr(record, "scope", None)
        if (
            scope is None
            or str(getattr(scope, "tenant_id", "")) != tenant_id
            or str(getattr(scope, "project_id", "")) != project_id
            or (
                actor_id is not None
                and str(getattr(scope, "owner_id", "")) != actor_id
            )
        ):
            raise SourceControlConnectionIntentError(
                "registered_remote_not_found", status_code=404
            )
        return {
            "kind": "registered_remote",
            "connector_type": expected_connector,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "owner_id": str(getattr(scope, "owner_id", "")),
            "remote_id": remote_id,
            "authorization_kind": str(
                getattr(record, "authorization_kind", "")
            ),
            "remote_url": str(getattr(record, "remote_url", "")),
            "repository": getattr(record, "repository", None),
        }


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ResolvedSourceControlConnectionIntent",
    "SourceControlConnectionIntentError",
    "SourceControlConnectionIntentResolver",
]
