"""Hub authorization for assignment-bound payload artifact downloads."""

from __future__ import annotations

import hmac
import time
from collections.abc import Mapping
from typing import Any


_PAYLOAD_MEDIA_TYPE = "application/vnd.ananta.knowledge-index-job+json"


class KnowledgeIndexPayloadAuthorizationError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 401) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class KnowledgeIndexPayloadCapabilityAuthorizer:
    """Validate one signed capability against live Hub authority."""

    def __init__(
        self,
        *,
        execution_binding_service: Any,
        manifest_verifier: Any,
        agent_repository: Any,
        clock_ms=lambda: int(time.time() * 1000),
    ) -> None:
        self._bindings = execution_binding_service
        self._manifest_verifier = manifest_verifier
        self._agents = agent_repository
        self._clock_ms = clock_ms

    def authorize(
        self,
        *,
        artifact_id: str,
        artifact_sha256: str,
        artifact_size_bytes: int,
        artifact_media_type: str,
        manifest: Mapping[str, Any],
        worker_id: str,
        worker_url: str,
    ) -> str:
        if not self._manifest_verifier.verify_manifest(manifest):
            self._deny("knowledge_index_payload_capability_signature_invalid")
        if int(manifest.get("grant_expires_at_epoch_ms") or 0) <= int(
            self._clock_ms()
        ):
            self._deny(
                "knowledge_index_payload_capability_expired",
                status_code=403,
            )

        normalized_worker_id = str(worker_id or "").strip()
        normalized_worker_url = str(worker_url or "").strip().rstrip("/")
        agent = self._agents.get_by_url(normalized_worker_url)
        if (
            agent is None
            or not hmac.compare_digest(
                str(getattr(agent, "name", "") or ""),
                normalized_worker_id,
            )
            or str(getattr(agent, "role", "") or "").strip().lower()
            != "worker"
            or not bool(getattr(agent, "registration_validated", False))
            or str(getattr(agent, "status", "") or "").strip().lower()
            not in {"online", "degraded", "busy"}
        ):
            self._deny(
                "knowledge_index_payload_worker_identity_invalid",
                status_code=403,
            )

        assignment_id = str(manifest.get("assignment_id") or "").strip()
        lease_id = str(manifest.get("lease_id") or "").strip()
        if not assignment_id or not lease_id:
            self._deny("knowledge_index_payload_binding_invalid")
        try:
            record = self._bindings.validate_delegated_payload_access(
                assignment_id=assignment_id,
                lease_id=lease_id,
                authenticated_worker_id=normalized_worker_id,
            )
        except Exception as exc:
            raise KnowledgeIndexPayloadAuthorizationError(
                "knowledge_index_payload_binding_invalid",
                status_code=403,
            ) from exc

        job = record.job.to_wire()
        job_id = str(job.get("job_id") or "")
        payload_reference = dict(
            (job.get("payload") or {}).get("payload_artifact_ref") or {}
        )
        supplied_reference = {
            "artifact_id": str(artifact_id or ""),
            "sha256": str(artifact_sha256 or "").lower(),
            "size_bytes": int(artifact_size_bytes),
            "media_type": str(artifact_media_type or "").lower(),
        }
        expected_reference = {
            "artifact_id": str(payload_reference.get("artifact_id") or ""),
            "sha256": str(payload_reference.get("sha256") or "").lower(),
            "size_bytes": int(payload_reference.get("size_bytes") or -1),
            "media_type": str(
                payload_reference.get("media_type") or ""
            ).lower(),
        }
        if (
            supplied_reference != expected_reference
            or supplied_reference["media_type"] != _PAYLOAD_MEDIA_TYPE
        ):
            self._deny("knowledge_index_payload_artifact_binding_mismatch")

        authority = dict(job.get("authority_binding") or {})
        assignment = dict(job.get("assignment") or {})
        content_manifest = dict(job.get("file_manifest") or {})
        expected_manifest = {
            "tenant_id": authority.get("tenant_id"),
            "project_id": authority.get("project_id"),
            "source_revision_id": authority.get("source_revision_id"),
            "source_revision_digest": authority.get(
                "source_revision_digest"
            ),
            "destination_id": authority.get("destination_id"),
            "destination_digest": authority.get("destination_digest"),
            "source_access_grant_id": authority.get(
                "source_access_grant_id"
            ),
            "source_access_grant_digest": authority.get(
                "source_access_grant_digest"
            ),
            "policy_digest": authority.get("policy_snapshot_digest"),
            "content_manifest_id": content_manifest.get("manifest_id"),
            "content_manifest_digest": content_manifest.get(
                "manifest_digest"
            ),
            "assignment_id": assignment.get("assignment_id"),
            "lease_id": assignment.get("lease_id"),
        }
        for field, expected_value in expected_manifest.items():
            if not hmac.compare_digest(
                str(manifest.get(field) or ""),
                str(expected_value or ""),
            ):
                self._deny(
                    "knowledge_index_payload_capability_binding_mismatch",
                    status_code=403,
                )
        return job_id

    @staticmethod
    def _deny(reason_code: str, *, status_code: int = 401) -> None:
        raise KnowledgeIndexPayloadAuthorizationError(
            reason_code,
            status_code=status_code,
        )


__all__ = [
    "KnowledgeIndexPayloadAuthorizationError",
    "KnowledgeIndexPayloadCapabilityAuthorizer",
]
