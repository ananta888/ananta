"""Bounded atomic storage for untrusted research artifacts."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ananta_contracts.research_training import ResearchArtifactManifestV1

if TYPE_CHECKING:
    from agent.services.research_training_quota_service import ResearchTrainingQuotaService


class ResearchTrainingArtifactService:
    def __init__(
        self,
        root: str | Path,
        *,
        max_artifact_bytes: int,
        quota: ResearchTrainingQuotaService | None = None,
    ) -> None:
        self._root = Path(root)
        self._max_artifact_bytes = int(max_artifact_bytes)
        if not 1 <= self._max_artifact_bytes <= 1 << 50:
            raise ValueError("research_artifact_limit_invalid")
        self._root.mkdir(parents=True, exist_ok=True)
        self._quota = quota

    def publish(
        self,
        *,
        manifest: Mapping[str, Any],
        content: bytes,
        reservation_id: str | None = None,
        retention_class: str = "checkpoint",
    ) -> dict[str, Any]:
        parsed = ResearchArtifactManifestV1.from_mapping(manifest)
        if not isinstance(content, bytes) or not 1 <= len(content) <= self._max_artifact_bytes:
            raise ValueError("research_artifact_content_size_invalid")
        digest = hashlib.sha256(content).hexdigest()
        if digest != parsed.artifact_digest or len(content) != parsed.size_bytes:
            raise ValueError("research_artifact_content_binding_invalid")
        if parsed.executable:
            raise PermissionError("research_executable_artifact_ingress_denied")
        relative = Path(parsed.tenant_id) / parsed.run_id / f"{digest}.bin"
        target = (self._root / relative).resolve()
        root = self._root.resolve()
        if root not in target.parents:
            raise PermissionError("research_artifact_path_escape")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("research_artifact_existing_digest_mismatch")
            if self._quota is not None:
                if not reservation_id:
                    raise ValueError("research_artifact_quota_reservation_required")
                self._quota.finalize(
                    tenant_id=parsed.tenant_id,
                    reservation_id=reservation_id,
                    artifact_digest=digest,
                    artifact_ref=relative.as_posix(),
                    actual_bytes=len(content),
                    retention_class=retention_class,
                )
            return self._projection(parsed, relative, replayed=True)
        if self._quota is not None and not reservation_id:
            raise ValueError("research_artifact_quota_reservation_required")
        descriptor, staging_name = tempfile.mkstemp(prefix=".research-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging_name, target)
        finally:
            if os.path.exists(staging_name):
                os.unlink(staging_name)
        if self._quota is not None:
            self._quota.finalize(
                tenant_id=parsed.tenant_id,
                reservation_id=str(reservation_id),
                artifact_digest=digest,
                artifact_ref=relative.as_posix(),
                actual_bytes=len(content),
                retention_class=retention_class,
            )
        return self._projection(parsed, relative, replayed=False)

    @staticmethod
    def _projection(
        manifest: ResearchArtifactManifestV1, relative: Path, *, replayed: bool
    ) -> dict[str, Any]:
        return {
            "schema": "ananta.research-training-artifact-receipt.v1",
            "artifact_digest": manifest.artifact_digest,
            "artifact_ref": relative.as_posix(),
            "size_bytes": manifest.size_bytes,
            "replayed": replayed,
            "executable": False,
            "human_intervention_required": False,
        }


__all__ = ["ResearchTrainingArtifactService"]
