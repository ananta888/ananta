"""Restart-stable content-addressed payload storage for knowledge indexing."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any, Protocol

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from agent.db_models import ArtifactDB, ArtifactVersionDB

_PAYLOAD_MEDIA_TYPE = "application/vnd.ananta.knowledge-index-job+json"
_PAYLOAD_FILENAME = "payload.json"
_PAYLOAD_ARTIFACT_PREFIX = "knowledge-index-payload-"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ImmutableArtifactStorePort(Protocol):
    def store_immutable_bytes(
        self,
        *,
        artifact_id: str,
        version_number: int,
        filename: str,
        content: bytes,
        expected_sha256: str,
        media_type: str,
    ) -> Mapping[str, Any]: ...


class ContentAddressedKnowledgeIndexPayloadStore:
    """Persist one immutable SQL artifact for each distinct payload digest.

    The filesystem object and both SQL identifiers are derived from the
    payload digest. Replays after a process restart therefore converge on the
    same reference, while the database primary keys close concurrent races.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        artifact_store: ImmutableArtifactStorePort,
    ) -> None:
        self._engine = engine
        self._artifacts = artifact_store

    def prepare_reference(
        self,
        *,
        content: bytes,
        fingerprint: str,
    ) -> dict[str, object]:
        normalized_fingerprint = str(fingerprint or "").strip().lower()
        if (
            _SHA256.fullmatch(normalized_fingerprint) is None
            or not isinstance(content, bytes)
            or not content
            or hashlib.sha256(content).hexdigest()
            != normalized_fingerprint
        ):
            raise ValueError("knowledge_index_payload_fingerprint_mismatch")
        return {
            "artifact_id": (
                _PAYLOAD_ARTIFACT_PREFIX + normalized_fingerprint
            ),
            "sha256": normalized_fingerprint,
            "size_bytes": len(content),
            "media_type": _PAYLOAD_MEDIA_TYPE,
            "encoding": "json",
        }

    def store_payload(
        self,
        *,
        content: bytes,
        fingerprint: str,
        created_by: str | None,
    ) -> dict[str, object]:
        reference = self.prepare_reference(
            content=content,
            fingerprint=fingerprint,
        )
        artifact_id = str(reference["artifact_id"])
        stored = dict(
            self._artifacts.store_immutable_bytes(
                artifact_id=artifact_id,
                version_number=1,
                filename=_PAYLOAD_FILENAME,
                content=content,
                expected_sha256=str(reference["sha256"]),
                media_type=_PAYLOAD_MEDIA_TYPE,
            )
        )
        self._validate_stored_bytes(stored, reference=reference)
        self._persist_metadata(
            reference=reference,
            stored=stored,
            created_by=str(created_by or "knowledge-index-api"),
        )
        return reference

    @staticmethod
    def _validate_stored_bytes(
        stored: Mapping[str, Any],
        *,
        reference: Mapping[str, object],
    ) -> None:
        if (
            str(stored.get("sha256") or "") != reference["sha256"]
            or int(stored.get("size_bytes") or -1)
            != reference["size_bytes"]
            or str(stored.get("media_type") or "")
            != _PAYLOAD_MEDIA_TYPE
            or str(stored.get("filename") or "") != _PAYLOAD_FILENAME
            or not str(stored.get("storage_path") or "").strip()
        ):
            raise ValueError("knowledge_index_payload_storage_mismatch")

    def _persist_metadata(
        self,
        *,
        reference: Mapping[str, object],
        stored: Mapping[str, Any],
        created_by: str,
    ) -> None:
        artifact_id = str(reference["artifact_id"])
        version_id = f"{artifact_id}-v1"
        for attempt in range(2):
            with Session(self._engine) as db:
                artifact = db.get(ArtifactDB, artifact_id)
                version = db.get(ArtifactVersionDB, version_id)
                self._validate_existing_metadata(
                    artifact=artifact,
                    version=version,
                    reference=reference,
                    stored=stored,
                )
                if version is None:
                    version = ArtifactVersionDB(
                        id=version_id,
                        artifact_id=artifact_id,
                        version_number=1,
                        storage_path=str(stored["storage_path"]),
                        original_filename=_PAYLOAD_FILENAME,
                        media_type=_PAYLOAD_MEDIA_TYPE,
                        size_bytes=int(reference["size_bytes"]),
                        sha256=str(reference["sha256"]),
                        version_metadata={
                            "versioning_ready": True,
                            "immutable": True,
                            "system_artifact_kind": (
                                "knowledge_index_job_payload"
                            ),
                        },
                    )
                    db.add(version)
                if artifact is None:
                    artifact = ArtifactDB(
                        id=artifact_id,
                        latest_version_id=version_id,
                        latest_sha256=str(reference["sha256"]),
                        latest_media_type=_PAYLOAD_MEDIA_TYPE,
                        latest_filename=_PAYLOAD_FILENAME,
                        size_bytes=int(reference["size_bytes"]),
                        status="stored",
                        created_by=created_by,
                        artifact_metadata={
                            "ingestion_mode": "content_addressed",
                            "system_artifact_kind": (
                                "knowledge_index_job_payload"
                            ),
                            "idempotency_fingerprint": str(
                                reference["sha256"]
                            ),
                        },
                    )
                    db.add(artifact)
                try:
                    db.commit()
                    return
                except IntegrityError:
                    db.rollback()
                    if attempt == 1:
                        raise ValueError(
                            "knowledge_index_payload_metadata_conflict"
                        ) from None
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_existing_metadata(
        *,
        artifact: ArtifactDB | None,
        version: ArtifactVersionDB | None,
        reference: Mapping[str, object],
        stored: Mapping[str, Any],
    ) -> None:
        artifact_id = str(reference["artifact_id"])
        fingerprint = str(reference["sha256"])
        size_bytes = int(reference["size_bytes"])
        if artifact is not None:
            metadata = dict(artifact.artifact_metadata or {})
            if (
                artifact.latest_version_id != f"{artifact_id}-v1"
                or artifact.latest_sha256 != fingerprint
                or artifact.latest_media_type != _PAYLOAD_MEDIA_TYPE
                or artifact.latest_filename != _PAYLOAD_FILENAME
                or int(artifact.size_bytes) != size_bytes
                or artifact.status != "stored"
                or metadata.get("system_artifact_kind")
                != "knowledge_index_job_payload"
                or metadata.get("idempotency_fingerprint") != fingerprint
            ):
                raise ValueError(
                    "knowledge_index_payload_metadata_conflict"
                )
        if version is not None and (
            version.artifact_id != artifact_id
            or int(version.version_number) != 1
            or version.storage_path != str(stored["storage_path"])
            or version.original_filename != _PAYLOAD_FILENAME
            or version.media_type != _PAYLOAD_MEDIA_TYPE
            or int(version.size_bytes) != size_bytes
            or version.sha256 != fingerprint
        ):
            raise ValueError("knowledge_index_payload_metadata_conflict")


__all__ = [
    "ContentAddressedKnowledgeIndexPayloadStore",
    "ImmutableArtifactStorePort",
]
