"""Planner payload adapter for admitted immutable remote Git artifacts."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from agent.db_models.source_control import SourceConnectionDB, SourceRevisionDB
from agent.services.hub_git_authorization_registry import (
    HubGitAuthorizationRegistryPort,
)
from agent.services.remote_source_payload_store import (
    SQLRemoteSourcePayloadStore,
    require_active_authorization,
)
from agent.services.source_control_index_authority_planner import (
    BoundSourceRevisionAuthority,
    BoundSourceRevisionPayload,
    BoundSourceRevisionPlanningError,
)
from agent.sources.git_source_connector_common import GitConnectorProviderError, GitSourceScope


class RemoteGitBoundSourcePayloadAdapter:
    def __init__(
        self,
        *,
        engine: Any,
        payload_store: SQLRemoteSourcePayloadStore,
        registry: HubGitAuthorizationRegistryPort,
    ) -> None:
        self._engine = engine
        self._payloads = payload_store
        self._registry = registry

    def load_bound_revision_payload(
        self, revision: BoundSourceRevisionAuthority
    ) -> BoundSourceRevisionPayload:
        if revision.connector_type not in {
            "git",
            "github",
            "generic_git",
            "github_repository",
        }:
            raise BoundSourceRevisionPlanningError(
                "source_revision_payload_connector_unsupported"
            )
        with Session(self._engine) as db:
            connection = db.get(SourceConnectionDB, revision.connection_id)
            row = db.get(SourceRevisionDB, revision.source_revision_id)
        if (
            connection is None
            or row is None
            or connection.state != "active"
            or connection.disabled_at_epoch is not None
            or connection.tombstoned_at_epoch is not None
            or row.revision_digest != revision.source_revision_digest
            or row.content_manifest_digest != revision.content_manifest_digest
        ):
            raise BoundSourceRevisionPlanningError("source_revision_stale")
        try:
            payload = self._payloads.load_bound(
                tenant_id=revision.tenant_id,
                project_id=revision.project_id,
                connection_id=revision.connection_id,
                source_revision_id=revision.source_revision_id,
            )
            _record, current_authorization_digest = require_active_authorization(
                registry=self._registry,
                scope=GitSourceScope(
                    tenant_id=payload.tenant_id,
                    project_id=payload.project_id,
                    owner_id=payload.owner_id,
                ),
                connection_ref=payload.connection_ref,
                repository_identifier=payload.repository_identifier,
            )
        except GitConnectorProviderError as exc:
            raise BoundSourceRevisionPlanningError(exc.reason_code) from None
        if (
            current_authorization_digest
            != payload.authorization_binding_digest
            or payload.source_revision_digest
            != revision.source_revision_digest
            or payload.manifest_digest != revision.content_manifest_digest
        ):
            raise BoundSourceRevisionPlanningError(
                "remote_source_payload_authorization_stale"
            )
        return BoundSourceRevisionPayload(
            source_revision_id=revision.source_revision_id,
            source_revision_digest=revision.source_revision_digest,
            content_manifest_digest=revision.content_manifest_digest,
            files=tuple(
                {
                    "relative_path": item.relative_path,
                    "sha256": item.content_digest,
                    "size_bytes": item.byte_size,
                }
                for item in payload.files
            ),
            records=tuple(
                {
                    "id": item.relative_path,
                    "content": item.content,
                    "metadata": {
                        "relative_path": item.relative_path,
                        "file_type": "text",
                    },
                }
                for item in payload.files
            ),
            payload_digest=payload.payload_digest,
            connection_id=revision.connection_id,
        )


__all__ = ["RemoteGitBoundSourcePayloadAdapter"]
