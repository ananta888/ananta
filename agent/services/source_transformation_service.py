from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from agent.sources.open_notebook_import_state import OpenNotebookImportStateStore
from agent.sources.source_transformation_templates import get_source_transformation_template


class SourceTransformationService:
    """Runs one governed transformation and persists its derived artifact."""

    def __init__(
        self,
        *,
        source_chat_service=None,
        ingestion_service=None,
        artifact_repository=None,
        state_store=None,
    ) -> None:
        if source_chat_service is None:
            from agent.services.source_chat_service import get_source_chat_service

            source_chat_service = get_source_chat_service()
        if ingestion_service is None:
            from agent.services.ingestion_service import get_ingestion_service

            ingestion_service = get_ingestion_service()
        if artifact_repository is None:
            from agent.repository import artifact_repo

            artifact_repository = artifact_repo
        self._source_chat_service = source_chat_service
        self._ingestion_service = ingestion_service
        self._artifact_repo = artifact_repository
        self._state_store = state_store or OpenNotebookImportStateStore()

    def transform(
        self,
        *,
        source_reference: dict[str, Any],
        transformation_id: str,
        execution_scope: str,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        template = get_source_transformation_template(transformation_id)
        if template is None:
            return self._failed("transformation_template_not_found")
        source_id = str(source_reference.get("source_id") or "").strip()
        snapshot_id = str(source_reference.get("snapshot_id") or "").strip()
        if not source_id or not snapshot_id:
            return self._failed("unverified_source_reference")
        state = self._state_store.load(source_id)
        state_key = f"transformation:{snapshot_id}:{transformation_id}"
        previous = dict((state.get("insights") or {}).get(state_key) or {})
        if previous.get("artifact_id"):
            return {
                "status": "completed",
                "reason_code": "duplicate_transformation",
                "artifact_id": previous["artifact_id"],
                "output_hash": previous.get("output_hash"),
                "idempotent": True,
            }
        try:
            chat_result = self._source_chat_service.answer(
                prompt=str(template["prompt_intent"]),
                source_ref=source_id,
                include_insights=False,
                include_notes=False,
                requested_llm_scope=str(execution_scope or "") or None,
            )
        except ValueError as exc:
            return self._failed(str(exc))
        output = str(chat_result.get("answer") or "").strip()
        if not output:
            return self._failed("empty_transformation_output")
        output_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            artifact, _version, _collection = self._ingestion_service.upload_artifact(
                filename=f"{transformation_id}-{snapshot_id}.md",
                content=output.encode("utf-8"),
                created_by=created_by,
                media_type="text/markdown",
                collection_name=None,
            )
            metadata = dict(getattr(artifact, "artifact_metadata", None) or {})
            metadata.update(
                {
                    "ingestion_mode": "open_notebook_transformation",
                    "source_system": "open_notebook",
                    "record_kind": "source_insight",
                    "parent_source_ref": dict(source_reference),
                    "transformation_id": transformation_id,
                    "output_hash": output_hash,
                    "created_at": created_at,
                }
            )
            artifact.artifact_metadata = metadata
            artifact = self._artifact_repo.save(artifact)
        except Exception:
            return self._failed("transformation_persistence_failed")
        state.setdefault("insights", {})[state_key] = {
            "artifact_id": str(artifact.id),
            "output_hash": output_hash,
        }
        self._state_store.save(source_id, state)
        return {
            "status": "completed",
            "reason_code": "ok",
            "artifact_id": str(artifact.id),
            "output_hash": output_hash,
            "created_at": created_at,
            "idempotent": False,
        }

    @staticmethod
    def _failed(reason_code: str) -> dict[str, Any]:
        return {"status": "failed", "reason_code": reason_code}
