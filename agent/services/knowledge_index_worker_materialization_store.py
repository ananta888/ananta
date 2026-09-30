"""Idempotent Hub persistence of materialized Worker index/run rows."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.db_models import KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.services.knowledge_index_consumption_policy import (
    KNOWLEDGE_INDEX_PROJECTED_STATE,
)
from agent.services.knowledge_index_worker_artifact_contract import (
    MATERIALIZATION_BINDING_METADATA_KEY,
    OUTPUT_FILENAMES,
    PENDING_PROJECTION_STATE,
)
from agent.services.knowledge_index_worker_artifact_staging import (
    verify_staged_file,
)


class KnowledgeIndexWorkerMaterializationStore:
    """Bind, replay and save knowledge-index rows under one Hub binding."""

    def __init__(
        self,
        *,
        knowledge_index_repository: Any,
        knowledge_index_run_repository: Any,
    ) -> None:
        self._knowledge_index_repository = knowledge_index_repository
        self._knowledge_index_run_repository = knowledge_index_run_repository

    def assert_existing_bindings(
        self,
        *,
        index_id: str,
        run_id: str,
        index_binding: Mapping[str, Any],
        run_binding: Mapping[str, Any],
    ) -> None:
        existing_index = self._knowledge_index_repository.get_by_id(
            index_id
        )
        if existing_index is not None:
            self._require_existing_binding(
                existing_index,
                metadata_field="index_metadata",
                expected=index_binding,
            )
        existing_run = self._knowledge_index_run_repository.get_by_id(
            run_id
        )
        if existing_run is not None:
            if (
                str(
                    getattr(
                        existing_run,
                        "knowledge_index_id",
                        "",
                    )
                    or ""
                )
                != index_id
            ):
                raise ValueError(
                    "knowledge_index_worker_materialization_binding_conflict"
                )
            self._require_existing_binding(
                existing_run,
                metadata_field="run_metadata",
                expected=run_binding,
            )

    def existing_materialized_unit(
        self,
        *,
        index_id: str,
        run_id: str,
        output_dir: Path,
        by_role: Mapping[str, Mapping[str, Any]],
        index_binding: Mapping[str, Any],
        run_binding: Mapping[str, Any],
    ) -> tuple[Any, Any] | None:
        """Return one exact local replay without reusing Worker authority."""

        existing_index = self._knowledge_index_repository.get_by_id(
            index_id
        )
        existing_run = self._knowledge_index_run_repository.get_by_id(run_id)
        if existing_index is None or existing_run is None:
            return None
        self._require_existing_binding(
            existing_index,
            metadata_field="index_metadata",
            expected=index_binding,
        )
        self._require_existing_binding(
            existing_run,
            metadata_field="run_metadata",
            expected=run_binding,
        )
        expected_output_dir = str(output_dir)
        expected_manifest_path = str(output_dir / "manifest.json")
        index_projection_state = self._existing_projection_state(
            existing_index,
            metadata_field="index_metadata",
        )
        run_projection_state = self._existing_projection_state(
            existing_run,
            metadata_field="run_metadata",
        )
        expected_status = (
            "completed"
            if index_projection_state == KNOWLEDGE_INDEX_PROJECTED_STATE
            else "pending_verification"
        )
        if (
            index_projection_state != run_projection_state
            or index_projection_state
            not in {
                PENDING_PROJECTION_STATE,
                KNOWLEDGE_INDEX_PROJECTED_STATE,
            }
            or
            str(getattr(existing_index, "status", "") or "")
            != expected_status
            or str(getattr(existing_run, "status", "") or "")
            != expected_status
            or str(
                getattr(existing_run, "knowledge_index_id", "") or ""
            )
            != index_id
            or str(getattr(existing_index, "latest_run_id", "") or "")
            != run_id
            or str(getattr(existing_index, "output_dir", "") or "")
            != expected_output_dir
            or str(getattr(existing_run, "output_dir", "") or "")
            != expected_output_dir
            or str(
                getattr(existing_index, "manifest_path", "") or ""
            )
            != expected_manifest_path
            or str(getattr(existing_run, "manifest_path", "") or "")
            != expected_manifest_path
        ):
            raise ValueError(
                "knowledge_index_worker_materialization_binding_conflict"
            )
        if output_dir.is_symlink() or not output_dir.is_dir():
            return None
        for role, reference in by_role.items():
            verify_staged_file(
                reference=reference,
                path=output_dir / OUTPUT_FILENAMES[role],
            )
        return existing_index, existing_run

    @staticmethod
    def _require_existing_binding(
        item: Any,
        *,
        metadata_field: str,
        expected: Mapping[str, Any],
    ) -> None:
        metadata = dict(getattr(item, metadata_field, None) or {})
        existing = metadata.get(
            MATERIALIZATION_BINDING_METADATA_KEY
        )
        if not isinstance(existing, Mapping):
            raise ValueError(
                "knowledge_index_worker_materialization_binding_conflict"
            )
        existing_binding = dict(existing)
        expected_binding = dict(expected)
        existing_state = existing_binding.pop("projection_state", None)
        expected_state = expected_binding.pop("projection_state", None)
        if (
            existing_binding != expected_binding
            or existing_state
            not in {
                PENDING_PROJECTION_STATE,
                KNOWLEDGE_INDEX_PROJECTED_STATE,
            }
            or expected_state
            not in {
                PENDING_PROJECTION_STATE,
                KNOWLEDGE_INDEX_PROJECTED_STATE,
            }
        ):
            raise ValueError(
                "knowledge_index_worker_materialization_binding_conflict"
            )

    @staticmethod
    def _existing_projection_state(
        item: Any,
        *,
        metadata_field: str,
    ) -> str:
        metadata = dict(getattr(item, metadata_field, None) or {})
        binding = metadata.get(MATERIALIZATION_BINDING_METADATA_KEY)
        return str(
            binding.get("projection_state")
            if isinstance(binding, Mapping)
            else ""
        )

    def save_index(
        self,
        payload: Mapping[str, Any],
        *,
        expected_binding: Mapping[str, Any],
    ) -> KnowledgeIndexDB:
        allowed = set(KnowledgeIndexDB.model_fields)
        values = {key: value for key, value in payload.items() if key in allowed}
        candidate = KnowledgeIndexDB.model_validate(values)
        existing = self._knowledge_index_repository.get_by_id(candidate.id)
        if existing is not None:
            self._require_existing_binding(
                existing,
                metadata_field="index_metadata",
                expected=expected_binding,
            )
            if (
                self._existing_projection_state(
                    existing,
                    metadata_field="index_metadata",
                )
                == KNOWLEDGE_INDEX_PROJECTED_STATE
                and expected_binding.get("projection_state")
                == PENDING_PROJECTION_STATE
            ):
                candidate.status = existing.status
                candidate.index_metadata = dict(
                    existing.index_metadata or {}
                )
            for field in allowed - {"id"}:
                setattr(existing, field, getattr(candidate, field))
            candidate = existing
        return self._knowledge_index_repository.save(candidate)

    def save_run(
        self,
        payload: Mapping[str, Any],
        *,
        expected_binding: Mapping[str, Any],
    ) -> KnowledgeIndexRunDB:
        allowed = set(KnowledgeIndexRunDB.model_fields)
        values = {key: value for key, value in payload.items() if key in allowed}
        candidate = KnowledgeIndexRunDB.model_validate(values)
        existing = self._knowledge_index_run_repository.get_by_id(candidate.id)
        if existing is not None:
            if existing.knowledge_index_id != candidate.knowledge_index_id:
                raise ValueError(
                    "knowledge_index_worker_materialization_binding_conflict"
                )
            self._require_existing_binding(
                existing,
                metadata_field="run_metadata",
                expected=expected_binding,
            )
            if (
                self._existing_projection_state(
                    existing,
                    metadata_field="run_metadata",
                )
                == KNOWLEDGE_INDEX_PROJECTED_STATE
                and expected_binding.get("projection_state")
                == PENDING_PROJECTION_STATE
            ):
                candidate.status = existing.status
                candidate.run_metadata = dict(existing.run_metadata or {})
            for field in allowed - {"id"}:
                setattr(existing, field, getattr(candidate, field))
            candidate = existing
        return self._knowledge_index_run_repository.save(candidate)


__all__ = ["KnowledgeIndexWorkerMaterializationStore"]
