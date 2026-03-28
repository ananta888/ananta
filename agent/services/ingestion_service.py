from __future__ import annotations

import time

from agent.db_models import ArtifactDB, ArtifactVersionDB, ExtractedDocumentDB, KnowledgeCollectionDB, KnowledgeLinkDB
from agent.repository import (
    artifact_repo,
    artifact_version_repo,
    extracted_document_repo,
    knowledge_collection_repo,
    knowledge_link_repo,
)
from agent.services.artifact_store import get_artifact_store
from agent.services.extraction_service import get_extraction_service


class IngestionService:
    """Coordinates raw storage, metadata persistence and extraction."""

    def __init__(self, artifact_store=None, extraction_service=None) -> None:
        self._artifact_store = artifact_store or get_artifact_store()
        self._extraction_service = extraction_service or get_extraction_service()

    def upload_artifact(
        self,
        *,
        filename: str,
        content: bytes,
        created_by: str | None,
        media_type: str | None = None,
        collection_name: str | None = None,
    ) -> tuple[ArtifactDB, ArtifactVersionDB, KnowledgeCollectionDB | None]:
        artifact = artifact_repo.save(
            ArtifactDB(
                created_by=created_by,
                status="stored",
                artifact_metadata={"ingestion_mode": "raw_artifact_store"},
            )
        )
        stored = self._artifact_store.store_bytes(
            artifact_id=artifact.id,
            version_number=1,
            filename=filename,
            content=content,
            media_type=media_type,
        )
        version = artifact_version_repo.save(
            ArtifactVersionDB(
                artifact_id=artifact.id,
                version_number=1,
                storage_path=stored["storage_path"],
                original_filename=stored["filename"],
                media_type=stored["media_type"],
                size_bytes=stored["size_bytes"],
                sha256=stored["sha256"],
                version_metadata={"versioning_ready": True},
            )
        )
        artifact.latest_version_id = version.id
        artifact.latest_sha256 = version.sha256
        artifact.latest_media_type = version.media_type
        artifact.latest_filename = version.original_filename
        artifact.size_bytes = version.size_bytes
        artifact.updated_at = time.time()
        artifact = artifact_repo.save(artifact)

        collection = None
        if collection_name:
            collection = knowledge_collection_repo.get_by_name(collection_name)
            if collection is None:
                collection = knowledge_collection_repo.save(
                    KnowledgeCollectionDB(name=collection_name, created_by=created_by)
                )
            knowledge_link_repo.save(
                KnowledgeLinkDB(
                    collection_id=collection.id,
                    artifact_id=artifact.id,
                    link_type="artifact",
                    link_metadata={"source": "artifact_upload", "collection_name": collection.name},
                )
            )

        return artifact, version, collection

    def extract_artifact(self, artifact_id: str) -> tuple[ArtifactDB | None, ArtifactVersionDB | None, ExtractedDocumentDB | None]:
        artifact = artifact_repo.get_by_id(artifact_id)
        if artifact is None or not artifact.latest_version_id:
            return artifact, None, None

        version = artifact_version_repo.get_by_id(artifact.latest_version_id)
        if version is None:
            return artifact, None, None

        extracted = self._extraction_service.extract(
            storage_path=version.storage_path,
            filename=version.original_filename,
            media_type=version.media_type,
        )
        document = extracted_document_repo.save(
            ExtractedDocumentDB(
                artifact_id=artifact.id,
                artifact_version_id=version.id,
                extraction_status=extracted["extraction_status"],
                extraction_mode=extracted["extraction_mode"],
                text_content=extracted["text_content"],
                document_metadata=extracted["metadata"],
            )
        )
        artifact.status = extracted["extraction_mode"]
        artifact.updated_at = time.time()
        artifact_repo.save(artifact)
        return artifact, version, document


ingestion_service = IngestionService()


def get_ingestion_service() -> IngestionService:
    return ingestion_service
