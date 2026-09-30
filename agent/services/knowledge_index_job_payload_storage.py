"""Content-addressed storage of large knowledge-index job payloads."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from agent.services.knowledge_index_job_contract import PAYLOAD_MEDIA_TYPE
from agent.services.knowledge_index_job_ports import (
    KnowledgeIndexPayloadStorePort,
)


def normalize_payload_reference(
    raw_reference: Mapping[str, Any] | None,
    *,
    content: bytes,
) -> dict[str, Any]:
    """Accept a stored reference only when it names exactly ``content``."""

    reference = dict(raw_reference or {})
    artifact_id = str(reference.get("artifact_id") or "").strip()
    digest = str(reference.get("sha256") or "").strip().lower()
    media_type = str(reference.get("media_type") or "").strip().lower()
    size_bytes = int(reference.get("size_bytes") or -1)
    if not artifact_id:
        raise RuntimeError("knowledge_index_payload_artifact_id_missing")
    if digest != hashlib.sha256(content).hexdigest():
        raise RuntimeError("knowledge_index_payload_artifact_digest_mismatch")
    if size_bytes != len(content):
        raise RuntimeError("knowledge_index_payload_artifact_size_mismatch")
    if media_type != PAYLOAD_MEDIA_TYPE:
        raise RuntimeError("knowledge_index_payload_artifact_media_type_mismatch")
    return {
        "artifact_id": artifact_id,
        "sha256": digest,
        "size_bytes": size_bytes,
        "media_type": media_type,
        "encoding": "json",
    }


class KnowledgeIndexJobPayloadStorage:
    """Store, prepare and verify out-of-band job payload artifacts.

    Without an injected ``payload_store`` the legacy ingestion-artifact path
    is used, resolved lazily so importing the Hub service stays side-effect
    free.
    """

    def __init__(
        self,
        *,
        payload_store: KnowledgeIndexPayloadStorePort | None = None,
    ) -> None:
        self._payload_store = payload_store

    def store(
        self,
        *,
        content: bytes,
        fingerprint: str,
        created_by: str | None,
    ) -> dict[str, Any]:
        if self._payload_store is not None:
            raw_reference = self._payload_store.store_payload(
                content=content,
                fingerprint=fingerprint,
                created_by=created_by,
            )
        else:
            raw_reference = self._store_as_ingestion_artifact(
                content=content,
                fingerprint=fingerprint,
                created_by=created_by,
            )
        return normalize_payload_reference(
            raw_reference,
            content=content,
        )

    @staticmethod
    def _store_as_ingestion_artifact(
        *,
        content: bytes,
        fingerprint: str,
        created_by: str | None,
    ) -> dict[str, Any]:
        from agent.services.ingestion_service import get_ingestion_service

        artifact, version, _collection = get_ingestion_service().upload_artifact(
            filename=f"knowledge-index-payload-{fingerprint}.json",
            content=content,
            created_by=created_by or "knowledge-index-api",
            media_type=PAYLOAD_MEDIA_TYPE,
            artifact_metadata={
                "system_artifact_kind": (
                    "knowledge_index_job_payload"
                ),
                "idempotency_fingerprint": fingerprint,
            },
        )
        from agent.repository import artifact_repo

        artifact.artifact_metadata = {
            **dict(artifact.artifact_metadata or {}),
            "system_artifact_kind": "knowledge_index_job_payload",
            "idempotency_fingerprint": fingerprint,
        }
        artifact_repo.save(artifact)
        return {
            "artifact_id": artifact.id,
            "sha256": version.sha256,
            "size_bytes": version.size_bytes,
            "media_type": version.media_type,
        }

    def prepare_reference(
        self,
        *,
        content: bytes,
        fingerprint: str,
    ) -> dict[str, Any]:
        prepare_reference = getattr(
            self._payload_store,
            "prepare_reference",
            None,
        )
        if not callable(prepare_reference):
            raise RuntimeError(
                "knowledge_index_content_addressed_payload_store_required"
            )
        raw_reference = prepare_reference(
            content=content,
            fingerprint=fingerprint,
        )
        return normalize_payload_reference(
            raw_reference,
            content=content,
        )

    def store_prepared(
        self,
        *,
        content: bytes,
        fingerprint: str,
        created_by: str,
        expected_reference: Mapping[str, Any],
    ) -> None:
        stored_reference = self.store(
            content=content,
            fingerprint=fingerprint,
            created_by=created_by,
        )
        if stored_reference != dict(expected_reference):
            raise RuntimeError(
                "knowledge_index_payload_artifact_reference_changed"
            )


__all__ = [
    "KnowledgeIndexJobPayloadStorage",
    "normalize_payload_reference",
]
