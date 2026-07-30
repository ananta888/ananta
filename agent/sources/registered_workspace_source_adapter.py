"""Registry adapter for registered workspaces and local-directory aliases."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping

from agent.sources.registered_workspace_connector import (
    RegisteredWorkspaceConnector,
    RegisteredWorkspaceError,
    WorkspaceInventoryManifest,
)
from agent.sources.source_connectors import (
    ConnectorHealth,
    ConnectorRefreshRequest,
    SourceConnector,
    SourceConnectorError,
    SourceInventory,
    SourceRevisionResolution,
)


_CONNECTOR_TYPES = frozenset({"registered_workspace", "local_directory"})
_HOST_PATH_KEYS = frozenset(
    {
        "absolute_path",
        "directory",
        "host_path",
        "local_path",
        "path",
        "root",
        "workspace_root",
    }
)


def _contains_host_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key or "").strip().lower()
            if normalized in _HOST_PATH_KEYS or normalized.endswith("_url"):
                return True
            if _contains_host_path(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_host_path(item) for item in value)
    return False


def _relative_path_is_valid(value: str) -> bool:
    raw = str(value or ".").strip()
    if "\x00" in raw or "\\" in raw:
        return False
    path = PurePosixPath(raw)
    return not path.is_absolute() and all(
        part not in {"", ".."} for part in path.parts
    )


class RegisteredWorkspaceSourceAdapter:
    """Expose manifests without returning or delegating registered host roots."""

    def __init__(
        self,
        *,
        workspace_connector: RegisteredWorkspaceConnector,
        connector_type: str,
    ) -> None:
        normalized = str(connector_type or "").strip().lower()
        if normalized not in _CONNECTOR_TYPES:
            raise ValueError("workspace_connector_type_invalid")
        self.connector_type = normalized
        self._workspace_connector = workspace_connector

    def validate(self, descriptor: Mapping[str, Any]) -> tuple[str, ...]:
        errors: list[str] = []
        if not str(descriptor.get("source_id") or "").strip():
            errors.append("source_id_required")
        if (
            str(descriptor.get("source_type") or "").strip().lower()
            != self.connector_type
        ):
            errors.append("connector_type_mismatch")
        for name in ("tenant_id", "project_id", "workspace_id"):
            if not str(descriptor.get(name) or "").strip():
                errors.append(f"{name}_required")
        if _contains_host_path(descriptor):
            errors.append("workspace_host_path_forbidden")
        if not _relative_path_is_valid(
            str(descriptor.get("relative_path") or ".")
        ):
            errors.append("workspace_relative_path_invalid")
        return tuple(dict.fromkeys(errors))

    def resolve_revision(
        self,
        descriptor: Mapping[str, Any],
    ) -> SourceRevisionResolution:
        manifest = self._manifest(descriptor)
        return SourceRevisionResolution(
            revision_digest=manifest.revision_digest,
            immutable_ref=f"workspace-manifest:{manifest.manifest_digest}",
            metadata=self._public_manifest_metadata(manifest),
        )

    def inventory(self, descriptor: Mapping[str, Any]) -> SourceInventory:
        manifest = self._manifest(descriptor)
        return SourceInventory(
            item_count=len(manifest.entries),
            total_bytes=manifest.total_bytes,
            exclusions=(),
            manifest_digest=manifest.manifest_digest,
        )

    def refresh(
        self,
        descriptor: Mapping[str, Any],
        request: ConnectorRefreshRequest,
    ) -> Mapping[str, Any]:
        manifest = self._manifest(descriptor)
        return {
            "source_id": str(descriptor.get("source_id") or "").strip(),
            "status": "planned" if request.dry_run else "ok",
            "reason_code": "dry_run" if request.dry_run else None,
            "immutable_ref": f"workspace-manifest:{manifest.manifest_digest}",
            "revision_digest": manifest.revision_digest,
            "manifest_digest": manifest.manifest_digest,
            "item_count": len(manifest.entries),
            "total_bytes": manifest.total_bytes,
        }

    def health(self, descriptor: Mapping[str, Any]) -> ConnectorHealth:
        errors = self.validate(descriptor)
        if errors:
            return ConnectorHealth(status="degraded", reason_code=errors[0])
        try:
            self._manifest(descriptor)
        except SourceConnectorError as exc:
            return ConnectorHealth(status="degraded", reason_code=exc.reason_code)
        return ConnectorHealth(status="healthy")

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_type=self.connector_type,
            validator=self,
            revision_resolver=self,
            inventory_provider=self,
            refresher=self,
            health_provider=self,
        )

    def _manifest(
        self,
        descriptor: Mapping[str, Any],
    ) -> WorkspaceInventoryManifest:
        errors = self.validate(descriptor)
        if errors:
            raise SourceConnectorError(errors[0])
        try:
            return self._workspace_connector.inventory(
                tenant_id=str(descriptor.get("tenant_id") or "").strip(),
                project_id=str(descriptor.get("project_id") or "").strip(),
                workspace_id=str(descriptor.get("workspace_id") or "").strip(),
                relative_path=str(
                    descriptor.get("relative_path") or "."
                ).strip(),
            )
        except RegisteredWorkspaceError as exc:
            raise SourceConnectorError(exc.reason_code) from None

    def _public_manifest_metadata(
        self,
        manifest: WorkspaceInventoryManifest,
    ) -> Mapping[str, Any]:
        return {
            "connector_type": self.connector_type,
            "workspace_id": manifest.workspace_id,
            "relative_root": manifest.relative_root,
            "manifest_digest": manifest.manifest_digest,
        }


def build_registered_workspace_source_connectors(
    workspace_connector: RegisteredWorkspaceConnector,
) -> tuple[SourceConnector, SourceConnector]:
    return tuple(
        RegisteredWorkspaceSourceAdapter(
            workspace_connector=workspace_connector,
            connector_type=connector_type,
        ).connector()
        for connector_type in ("registered_workspace", "local_directory")
    )


__all__ = [
    "RegisteredWorkspaceSourceAdapter",
    "build_registered_workspace_source_connectors",
]
