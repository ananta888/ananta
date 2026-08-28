"""Worker-only execution boundary for Hub-delegated SIRA index operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from ananta_contracts.sira_index_operation import (
    CONTEXT_KEY,
    TASK_KIND,
    SiraIndexOperation,
)
from worker.retrieval.sira.incremental_enrichment import (
    EnrichmentLayerStore,
    plan_incremental_enrichment,
)

_SNAPSHOT_SCHEMA = "codecompass.sira-sync-snapshot.v1"
_MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
_MAX_DOCUMENTS = 250_000
_RESULT_FIELDS = frozenset(
    {
        "reason_code",
        "activation_digest",
        "base_layer_id",
        "delta_layer_count",
        "enriched_count",
        "unchanged_count",
        "tombstone_count",
        "invalidation_reason",
    }
)


class SiraIndexOperationRuntimePort(Protocol):
    def execute(self, command: SiraIndexOperation) -> Mapping[str, Any]: ...


class UnavailableSiraIndexOperationRuntime:
    def execute(self, command: SiraIndexOperation) -> Mapping[str, Any]:
        del command
        return {
            "status": "failed",
            "reason_code": "sira_index_operation_runtime_unavailable",
        }


class LocalSiraIndexOperationRuntime:
    """Use Worker-local roots; task payloads can never select filesystem paths."""

    def __init__(self, *, snapshot_root: str | Path, layer_root: str | Path) -> None:
        self._snapshot_root = Path(snapshot_root)
        self._layer_root = Path(layer_root)

    def execute(self, command: SiraIndexOperation) -> Mapping[str, Any]:
        store = EnrichmentLayerStore(root=self._repository_layer_root(command))
        if command.operation == "compact":
            active = store.compact()
            return {
                "status": "completed",
                "reason_code": "sira_index_compaction_completed",
                "operation_id": command.operation_id,
                "activation_digest": str(active["activation_digest"]),
                "base_layer_id": str(active["base_layer_id"]),
                "delta_layer_count": 0,
            }
        snapshot = self._load_snapshot(command)
        binding = self._validate_snapshot(snapshot, command)
        current_documents = list(snapshot["documents"])
        enrichments = dict(snapshot["enrichments"])
        active = store.active()
        materialized = store.materialize_active() if active is not None else {}
        previous_documents = [
            {
                "record_id": record_id,
                "document_hash": str(artifact.get("source_document_hash") or ""),
            }
            for record_id, artifact in materialized.items()
        ]
        previous_binding = dict(active.get("binding") or {}) if active else {}
        changes = plan_incremental_enrichment(
            previous_documents=previous_documents,
            current_documents=current_documents,
            previous_profile_digest=str(previous_binding.get("profile_digest") or ""),
            current_profile_digest=str(binding["profile_digest"]),
        )
        missing = sorted(
            record_id for record_id in changes.enrich_record_ids if not isinstance(enrichments.get(record_id), Mapping)
        )
        if missing:
            raise ValueError("sira_sync_enrichment_missing")
        artifacts = [dict(enrichments[record_id]) for record_id in changes.enrich_record_ids]
        if active is None:
            layer = store.write_layer(
                layer_kind="base",
                parent_layer_id="",
                artifacts=artifacts,
                tombstone_record_ids=(),
                binding=binding,
            )
            activated = store.activate(
                base_layer_id=str(layer["layer_id"]),
                delta_layer_ids=(),
                binding=binding,
            )
        elif not changes.enrich_record_ids and not changes.tombstone_record_ids:
            activated = active
        else:
            prior_deltas = list(active.get("delta_layer_ids") or [])
            parent = str(prior_deltas[-1] if prior_deltas else active["base_layer_id"])
            layer = store.write_layer(
                layer_kind="delta",
                parent_layer_id=parent,
                artifacts=artifacts,
                tombstone_record_ids=changes.tombstone_record_ids,
                binding=binding,
            )
            activated = store.activate(
                base_layer_id=str(active["base_layer_id"]),
                delta_layer_ids=[*prior_deltas, str(layer["layer_id"])],
                binding=binding,
            )
        return {
            "status": "completed",
            "reason_code": "sira_index_sync_completed",
            "operation_id": command.operation_id,
            "activation_digest": str(activated["activation_digest"]),
            "enriched_count": len(changes.enrich_record_ids),
            "unchanged_count": len(changes.unchanged_record_ids),
            "tombstone_count": len(changes.tombstone_record_ids),
            "delta_layer_count": len(activated.get("delta_layer_ids") or []),
            "invalidation_reason": changes.invalidation_reason,
        }

    def _load_snapshot(self, command: SiraIndexOperation) -> Mapping[str, Any]:
        path = self._snapshot_root / f"{command.snapshot_artifact_id}.json"
        try:
            resolved = path.resolve(strict=True)
            root = self._snapshot_root.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ValueError("sira_sync_snapshot_unavailable") from exc
        if resolved.stat().st_size > _MAX_SNAPSHOT_BYTES:
            raise ValueError("sira_sync_snapshot_too_large")
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("sira_sync_snapshot_invalid") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("sira_sync_snapshot_invalid")
        return payload

    @staticmethod
    def _validate_snapshot(
        snapshot: Mapping[str, Any],
        command: SiraIndexOperation,
    ) -> dict[str, Any]:
        if set(snapshot) != {"schema", "binding", "documents", "enrichments"}:
            raise ValueError("sira_sync_snapshot_schema_invalid")
        if snapshot.get("schema") != _SNAPSHOT_SCHEMA:
            raise ValueError("sira_sync_snapshot_schema_invalid")
        documents = snapshot.get("documents")
        enrichments = snapshot.get("enrichments")
        binding = snapshot.get("binding")
        if (
            not isinstance(documents, list)
            or len(documents) > _MAX_DOCUMENTS
            or not all(isinstance(item, Mapping) for item in documents)
            or not isinstance(enrichments, Mapping)
            or not isinstance(binding, Mapping)
        ):
            raise ValueError("sira_sync_snapshot_content_invalid")
        expected_binding_fields = {
            "tenant_id",
            "project_id",
            "repository_id",
            "repository_revision",
            "source_manifest_hash",
            "index_digest",
            "statistics_digest",
            "profile_version",
            "profile_digest",
        }
        if set(binding) != expected_binding_fields:
            raise ValueError("sira_sync_binding_schema_invalid")
        safe_binding = {key: str(binding.get(key) or "").strip() for key in expected_binding_fields}
        if any(not value for value in safe_binding.values()):
            raise ValueError("sira_sync_binding_value_required")
        if (
            safe_binding["tenant_id"] != command.tenant_id
            or safe_binding["project_id"] != command.project_id
            or safe_binding["repository_id"] != command.repository_id
        ):
            raise ValueError("sira_sync_binding_scope_mismatch")
        record_ids: set[str] = set()
        document_hashes: dict[str, str] = {}
        for document in documents:
            record_id = str(document.get("record_id") or "").strip()
            document_hash = str(document.get("document_hash") or "").strip()
            if not record_id or not document_hash or record_id in record_ids:
                raise ValueError("sira_sync_document_identity_invalid")
            record_ids.add(record_id)
            document_hashes[record_id] = document_hash
        if any(str(key) not in record_ids for key in enrichments):
            raise ValueError("sira_sync_enrichment_orphaned")
        for raw_record_id, raw_artifact in enrichments.items():
            record_id = str(raw_record_id)
            if not isinstance(raw_artifact, Mapping):
                raise ValueError("sira_sync_enrichment_invalid")
            artifact = dict(raw_artifact)
            if (
                artifact.get("schema") != "codecompass.sira-enrichment.v1"
                or str(artifact.get("source_chunk_id") or "") != record_id
                or str(artifact.get("source_document_hash") or "") != document_hashes[record_id]
            ):
                raise ValueError("sira_sync_enrichment_binding_mismatch")
            artifact_digest = str(artifact.pop("artifact_digest", ""))
            expected_digest = hashlib.sha256(
                json.dumps(
                    artifact,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            if artifact_digest != expected_digest:
                raise ValueError("sira_sync_enrichment_digest_mismatch")
        return safe_binding

    def _repository_layer_root(self, command: SiraIndexOperation) -> Path:
        namespace = hashlib.sha256(
            f"{command.tenant_id}:{command.project_id}:{command.repository_id}".encode("utf-8")
        ).hexdigest()
        return self._layer_root / namespace


class SiraIndexOperationTaskHandler:
    """Validate and execute one delegated operation without orchestration."""

    def __init__(self, runtime: SiraIndexOperationRuntimePort) -> None:
        self._runtime = runtime

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        command = self._resolve(kwargs)
        return {
            "proposal_id": f"{command.operation_id}-proposal",
            "strategy_id": "deterministic_handler",
            "command": None,
            "tool_calls": [
                {
                    "name": TASK_KIND,
                    "arguments": {
                        "operation_id": command.operation_id,
                        "operation": command.operation,
                    },
                }
            ],
            "safety_flags": {
                "worker_only": True,
                "worker_orchestration_forbidden": True,
                "human_approval_required": False,
            },
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        try:
            command = self._resolve(kwargs)
        except Exception as exc:
            return {
                "schema": "ananta.sira-index-operation-result.v1",
                "operation_id": str(kwargs.get("tid") or "")[:191],
                "status": "failed",
                "reason_code": self._reason_code(exc),
            }
        try:
            result = dict(self._runtime.execute(command) or {})
        except Exception as exc:
            return {
                "schema": "ananta.sira-index-operation-result.v1",
                "operation_id": command.operation_id,
                "status": "failed",
                "reason_code": self._reason_code(exc),
            }
        status = str(result.get("status") or "").strip().lower()
        if status not in {"completed", "failed"}:
            status = "failed"
            result = {"reason_code": "sira_index_operation_result_invalid"}
        return {
            "schema": "ananta.sira-index-operation-result.v1",
            "operation_id": command.operation_id,
            "status": status,
            **{key: value for key, value in result.items() if key in _RESULT_FIELDS},
        }

    @staticmethod
    def _reason_code(exc: Exception) -> str:
        reason = str(exc).strip()
        if reason.startswith("sira_") and len(reason) <= 160:
            return reason
        return f"sira_index_operation_failed:{type(exc).__name__}"

    @staticmethod
    def _resolve(kwargs: Mapping[str, Any]) -> SiraIndexOperation:
        task = kwargs.get("task")
        if not isinstance(task, Mapping):
            raise ValueError("sira_index_operation_task_required")
        if str(task.get("task_kind") or "").strip().lower() != TASK_KIND:
            raise ValueError("sira_index_operation_task_kind_invalid")
        context = task.get("worker_execution_context")
        command = context.get(CONTEXT_KEY) if isinstance(context, Mapping) else None
        return SiraIndexOperation.from_mapping(command)


__all__ = [
    "LocalSiraIndexOperationRuntime",
    "SiraIndexOperationRuntimePort",
    "SiraIndexOperationTaskHandler",
    "UnavailableSiraIndexOperationRuntime",
]
