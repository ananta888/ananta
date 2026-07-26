"""Build lease-bound Recovery manifests from real workspace files."""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ananta_contracts.recovery_artifact_ingress import (
    MAX_RECOVERY_ARTIFACT_BYTES,
    MAX_RECOVERY_ARTIFACT_COUNT,
    MAX_RECOVERY_ARTIFACT_TOTAL_BYTES,
    build_recovery_artifact_ingress_manifest,
)

_SOURCE_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$"
)


class RecoveryWorkspaceArtifactManifestError(RuntimeError):
    """Raised when an artifact claim is not backed by one workspace file."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class RecoveryWorkspaceArtifactManifest:
    manifest: dict[str, Any]
    descriptors: tuple[dict[str, Any], ...]


class RecoveryWorkspaceArtifactManifestService:
    """Create the same metadata contract for remote and trusted-local execution."""

    def __init__(
        self,
        *,
        workspace_service_provider: Callable[[], Any] | None = None,
        workspace_file_reader_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._workspace_service_provider = workspace_service_provider
        self._workspace_file_reader_provider = (
            workspace_file_reader_provider
        )

    def build(
        self,
        *,
        task: Mapping[str, Any],
        artifacts: list[dict[str, Any]],
        lease_token: str,
        request_fingerprint: str,
        executor_id: str,
        executor_url: str,
    ) -> RecoveryWorkspaceArtifactManifest:
        task_id = str(task.get("id") or "").strip()
        normalized_executor_id = str(executor_id or "").strip()
        normalized_executor_url = str(executor_url or "").strip().rstrip(
            "/"
        )
        if not all(
            (
                task_id,
                normalized_executor_id,
                normalized_executor_url,
                lease_token,
                request_fingerprint,
            )
        ):
            raise RecoveryWorkspaceArtifactManifestError(
                "recovery_artifact_manifest_identity_unavailable"
            )
        if (
            not isinstance(artifacts, list)
            or not artifacts
            or len(artifacts) > MAX_RECOVERY_ARTIFACT_COUNT
        ):
            raise RecoveryWorkspaceArtifactManifestError(
                "recovery_artifact_count_invalid"
            )
        if any(
            not isinstance(artifact, Mapping)
            for artifact in artifacts
        ):
            raise RecoveryWorkspaceArtifactManifestError(
                "recovery_artifact_descriptor_invalid"
            )
        workspace_root = (
            self._workspace_service()
            .resolve_workspace_dir_for_read(
                task=dict(task),
                agent_name=normalized_executor_id,
            )
        )
        descriptors_list: list[dict[str, Any]] = []
        total_bytes = 0
        for index, artifact in enumerate(artifacts):
            descriptor = self._descriptor(
                workspace_root=workspace_root,
                artifact=artifact,
                source_index=index,
            )
            total_bytes += int(descriptor["size_bytes"])
            if total_bytes > MAX_RECOVERY_ARTIFACT_TOTAL_BYTES:
                raise RecoveryWorkspaceArtifactManifestError(
                    "recovery_artifact_total_size_exceeded"
                )
            descriptors_list.append(descriptor)
        descriptors = tuple(descriptors_list)
        manifest = build_recovery_artifact_ingress_manifest(
            task_id=task_id,
            worker_url=normalized_executor_url,
            request_fingerprint=request_fingerprint,
            lease_token=lease_token,
            artifacts=descriptors,
        )
        return RecoveryWorkspaceArtifactManifest(
            manifest=manifest,
            descriptors=descriptors,
        )

    def _workspace_service(self) -> Any:
        if self._workspace_service_provider is not None:
            return self._workspace_service_provider()
        from agent.services.worker_workspace_service import (
            get_worker_workspace_service,
        )

        return get_worker_workspace_service()

    def _workspace_file_reader(self) -> Any:
        if self._workspace_file_reader_provider is not None:
            return self._workspace_file_reader_provider()
        from agent.services.recovery_workspace_file_reader import (
            get_recovery_workspace_file_reader,
        )

        return get_recovery_workspace_file_reader()

    def _descriptor(
        self,
        *,
        workspace_root: Path,
        artifact: Mapping[str, Any],
        source_index: int,
    ) -> dict[str, Any]:
        relative_path = str(
            artifact.get("workspace_relative_path") or ""
        )
        if not relative_path:
            raise RecoveryWorkspaceArtifactManifestError(
                "recovery_artifact_workspace_path_required"
            )
        from agent.services.recovery_workspace_file_reader import (
            RecoveryWorkspaceFileReadError,
        )

        try:
            snapshot = self._workspace_file_reader().read(
                workspace_root=workspace_root,
                relative_path=relative_path,
                maximum_bytes=MAX_RECOVERY_ARTIFACT_BYTES,
            )
        except RecoveryWorkspaceFileReadError as exc:
            raise RecoveryWorkspaceArtifactManifestError(
                exc.reason_code
            ) from exc
        path = snapshot.resolved_path
        content = snapshot.content
        size_bytes = snapshot.size_bytes
        actual_hash = hashlib.sha256(content).hexdigest()
        claimed_hash = str(
            artifact.get("content_hash") or ""
        ).strip()
        if (
            len(content) != size_bytes
            or len(claimed_hash) != 64
            or not hmac.compare_digest(
                claimed_hash,
                actual_hash,
            )
        ):
            raise RecoveryWorkspaceArtifactManifestError(
                "recovery_artifact_worker_hash_mismatch"
            )
        filename = str(
            artifact.get("filename") or path.name
        ).strip()
        media_type = str(
            artifact.get("media_type")
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        ).strip()
        return {
            "source_index": source_index,
            "kind": str(
                artifact.get("kind") or "workspace_file"
            ).strip(),
            "workspace_path": relative_path,
            "relative_path": relative_path,
            "filename": filename,
            "media_type": media_type,
            "size_bytes": size_bytes,
            "sha256": actual_hash,
            "worker_artifact_id": _optional_source_id(
                artifact.get("artifact_id")
            ),
            "worker_artifact_version_id": _optional_source_id(
                artifact.get("artifact_version_id")
            ),
        }


def _optional_source_id(value: object) -> str | None:
    raw = str(value or "").strip()
    return raw if _SOURCE_ID.fullmatch(raw) else None


_SERVICE = RecoveryWorkspaceArtifactManifestService()


def get_recovery_workspace_artifact_manifest_service() -> (
    RecoveryWorkspaceArtifactManifestService
):
    return _SERVICE


__all__ = [
    "RecoveryWorkspaceArtifactManifest",
    "RecoveryWorkspaceArtifactManifestError",
    "RecoveryWorkspaceArtifactManifestService",
    "get_recovery_workspace_artifact_manifest_service",
]
