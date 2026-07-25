from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from worker.retrieval.qdrant_collection_schema import compare_compatibility
from worker.retrieval.qdrant_vector_store import (
    QdrantVectorStore,
    emit_operation_observation,
    observation_outcome,
)
from worker.retrieval.vector_encoding import VectorEncoder, VectorEncodingProfile
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorScope,
)


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    status: str
    reason: str
    source_digest: str
    source_entries: int
    compatible_entries: int
    target_collection: str | None
    dimensions: int
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MigrationCheckpoint:
    source_digest: str
    collection_name: str
    next_offset: int


@dataclass(frozen=True, slots=True)
class MigrationResult:
    result: IndexWriteResult
    checkpoint: MigrationCheckpoint | None
    activated: bool
    source_preserved: bool = True


class JsonToQdrantMigrator:
    def __init__(
        self,
        store: QdrantVectorStore,
        *,
        observer: Any = None,
    ):
        self._store = store
        self._observer = observer

    @staticmethod
    def _load(source_path: str | Path) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
        raw = Path(source_path).read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        state = dict(payload.get("state") or {})
        entries = [
            dict(item)
            for item in list(payload.get("entries") or [])
            if isinstance(item, Mapping)
        ]
        return raw, state, entries

    @staticmethod
    def _source_compatibility(state: Mapping[str, Any]) -> CompatibilitySpec | None:
        dimensions = int(state.get("embedding_dimensions") or 0)
        provider = str(state.get("embedding_provider") or "")
        model = str(state.get("embedding_model_name") or "")
        profile = str(state.get("embedding_text_profile") or "")
        if dimensions <= 0 or not provider or not model or not profile:
            return None
        encoding_profile = dict(state.get("vector_encoding_profile") or {})
        encoding = str(encoding_profile.get("mode") or "float32")
        if encoding == "off":
            encoding = "float32"
        return CompatibilitySpec(
            dimensions=dimensions,
            distance="cosine",
            provider=provider,
            model=model,
            profile=profile,
            encoding=encoding,
            config_hash=str(state.get("embedding_provider_config_hash") or ""),
            schema_version="vector_store.v1",
            manifest_hash=str(state.get("manifest_hash") or ""),
        )

    @staticmethod
    def _entry_vector(entry: Mapping[str, Any], state: Mapping[str, Any]) -> tuple[float, ...]:
        if isinstance(entry.get("vector"), list):
            return tuple(float(value) for value in list(entry.get("vector") or []))
        encoded = entry.get("encoded_vector")
        if isinstance(encoded, Mapping):
            profile = VectorEncodingProfile.from_config(
                dict(state.get("vector_encoding_profile") or {})
            )
            return tuple(VectorEncoder(profile).decode(dict(encoded)))
        return ()

    @classmethod
    def _prepared_points(
        cls,
        entries: Sequence[Mapping[str, Any]],
        state: Mapping[str, Any],
        scope: VectorScope,
    ) -> list[PreparedVectorPoint]:
        points: list[PreparedVectorPoint] = []
        for entry in entries:
            vector = cls._entry_vector(entry, state)
            record_id = str(entry.get("record_id") or "")
            if not record_id or not vector:
                continue
            payload = {
                key: entry.get(key)
                for key in (
                    "kind",
                    "file",
                    "parent_id",
                    "role_labels",
                    "importance_score",
                    "source_scope",
                )
            }
            source_hash = str(entry.get("source_hash") or "")
            if not source_hash:
                source_hash = hashlib.sha256(
                    json.dumps(
                        {"record_id": record_id, "payload": payload, "vector": vector},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            points.append(
                PreparedVectorPoint(
                    record_id=record_id,
                    vector=vector,
                    scope=scope,
                    payload=payload,
                    source_hash=source_hash,
                )
            )
        return points

    def dry_run(
        self,
        source_path: str | Path,
        *,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
    ) -> MigrationPlan:
        raw, state, entries = self._load(source_path)
        digest = hashlib.sha256(raw).hexdigest()
        if str(state.get("schema") or "") not in {
            "codecompass_vector_index.v1",
            "codecompass_vector_index.v2",
        }:
            return MigrationPlan(
                "blocked",
                "migration_required",
                digest,
                len(entries),
                0,
                None,
                int(state.get("embedding_dimensions") or 0),
                ("legacy_schema_missing_or_unknown",),
            )
        source_compatibility = self._source_compatibility(state)
        if source_compatibility is None:
            return MigrationPlan(
                "blocked",
                "rebuild_required",
                digest,
                len(entries),
                0,
                None,
                int(state.get("embedding_dimensions") or 0),
                ("source_state_incomplete",),
            )
        report = compare_compatibility(compatibility, asdict(source_compatibility))
        if not report.compatible:
            return MigrationPlan(
                "blocked",
                report.reason,
                digest,
                len(entries),
                0,
                None,
                source_compatibility.dimensions,
                (report.reason,),
            )
        points = self._prepared_points(entries, state, scope)
        conflicts = () if len(points) == len(entries) else ("entries_without_vector_or_record_id",)
        status = "ready" if not conflicts else "blocked"
        reason = "migration_ready" if not conflicts else "rebuild_required"
        return MigrationPlan(
            status,
            reason,
            digest,
            len(entries),
            len(points),
            None,
            source_compatibility.dimensions,
            conflicts,
        )

    def migrate(
        self,
        source_path: str | Path,
        *,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
        checkpoint: MigrationCheckpoint | None = None,
        batch_size: int = 128,
        max_batches: int | None = None,
    ) -> MigrationResult:
        started = time.monotonic()
        plan = self.dry_run(source_path, scope=scope, compatibility=compatibility)
        if plan.status != "ready":
            result = IndexWriteResult(
                status="failed",
                mode="migrate",
                reason=plan.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "conflicts": plan.conflicts},
                failed=plan.source_entries,
            )
            self._observe(result, started)
            return MigrationResult(result, None, False)
        raw, state, entries = self._load(source_path)
        points = self._prepared_points(entries, state, scope)
        collection = self._store.prepare_collection(
            scope,
            compatibility,
            index_version=plan.source_digest,
        )
        offset = 0
        if checkpoint is not None:
            if (
                checkpoint.source_digest != plan.source_digest
                or checkpoint.collection_name != collection
                or not 0 <= checkpoint.next_offset <= len(points)
            ):
                result = IndexWriteResult(
                    status="failed",
                    mode="migrate",
                    reason="migration_checkpoint_invalid",
                    indexed_documents=0,
                    diagnostics={"status": "failed"},
                    failed=len(points),
                )
                self._observe(result, started)
                return MigrationResult(result, None, False)
            offset = checkpoint.next_offset
        total_upserted = total_skipped = total_failed = 0
        batches = 0
        while offset < len(points):
            if max_batches is not None and batches >= max(0, int(max_batches)):
                break
            batch = points[offset : offset + max(1, int(batch_size))]
            write = self._store.upsert_to_collection(
                collection,
                batch,
                compatibility,
                batch_size=batch_size,
            )
            total_upserted += write.upserted
            total_skipped += write.skipped
            total_failed += write.failed
            if write.failed:
                result = IndexWriteResult(
                    status="partial",
                    mode="migrate",
                    reason=write.reason,
                    indexed_documents=total_upserted,
                    diagnostics={"status": "partial", "next_offset": offset},
                    upserted=total_upserted,
                    skipped=total_skipped,
                    failed=total_failed,
                )
                next_checkpoint = MigrationCheckpoint(plan.source_digest, collection, offset)
                self._observe(result, started)
                return MigrationResult(result, next_checkpoint, False)
            offset += len(batch)
            batches += 1
        if offset < len(points):
            result = IndexWriteResult(
                status="partial",
                mode="migrate",
                reason="migration_paused",
                indexed_documents=total_upserted,
                diagnostics={"status": "partial", "next_offset": offset},
                upserted=total_upserted,
                skipped=total_skipped,
            )
            next_checkpoint = MigrationCheckpoint(plan.source_digest, collection, offset)
            self._observe(result, started)
            return MigrationResult(result, next_checkpoint, False)
        self._store.activate_collection(scope, collection, compatibility)
        result = IndexWriteResult(
            status="ok",
            mode="migrate",
            reason="migrated",
            indexed_documents=total_upserted,
            diagnostics={
                "status": "ok",
                "source_digest": plan.source_digest,
                "source_preserved": True,
            },
            upserted=total_upserted,
            skipped=total_skipped,
            failed=0,
        )
        self._observe(result, started)
        return MigrationResult(result, None, True)

    def _observe(self, result: IndexWriteResult, started: float) -> None:
        emit_operation_observation(
            self._observer,
            operation="migrate",
            outcome=observation_outcome(result.status),
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={
                "upserted": result.upserted,
                "skipped": result.skipped,
                "failed": result.failed,
            },
        )
