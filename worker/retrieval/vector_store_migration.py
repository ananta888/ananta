from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from worker.retrieval.qdrant_client_port import QdrantClientError
from worker.retrieval.qdrant_collection_schema import (
    QdrantSchemaError,
    compare_compatibility,
)
from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_encoding import VectorEncoder, VectorEncodingProfile
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorScope,
)
from worker.retrieval.vector_store_observer import (
    emit_operation_observation,
    observation_outcome,
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
    distance: str = ""
    scope_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class MigrationCheckpoint:
    source_digest: str
    collection_name: str
    next_offset: int
    scope_fingerprint: str = ""
    idempotency_key_hash: str = ""


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
    def _load(
        source: str | Path | bytes,
    ) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
        raw = source if isinstance(source, bytes) else Path(source).read_bytes()
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
        distance = str(state.get("distance") or "").strip().lower()
        provider = str(state.get("embedding_provider") or "")
        model = str(state.get("embedding_model_name") or "")
        profile = str(state.get("embedding_text_profile") or "")
        config_hash = str(state.get("embedding_provider_config_hash") or "")
        manifest_hash = str(state.get("manifest_hash") or "")
        encoding = str(state.get("vector_encoding_config_hash") or "").strip()
        if not encoding:
            encoding_profile = state.get("vector_encoding_profile")
            if not isinstance(encoding_profile, Mapping):
                return None
            encoding = str(encoding_profile.get("mode") or "").strip().lower()
            if encoding == "off":
                encoding = "float32"
        if (
            dimensions <= 0
            or not distance
            or not provider
            or not model
            or not profile
            or not config_hash
            or not manifest_hash
            or not encoding
        ):
            return None
        try:
            return CompatibilitySpec(
                dimensions=dimensions,
                distance=distance,
                provider=provider,
                model=model,
                profile=profile,
                encoding=encoding,
                config_hash=config_hash,
                schema_version="vector_store.v1",
                manifest_hash=manifest_hash,
            )
        except (TypeError, ValueError):
            return None

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

    @staticmethod
    def _entry_matches_scope(
        entry: Mapping[str, Any],
        scope: VectorScope,
    ) -> bool:
        """Accept absent legacy fields, but never relabel an explicit scope."""

        for field_name, expected in scope.as_dict().items():
            actual = entry.get(field_name)
            if actual is None or not str(actual).strip():
                continue
            if str(actual).strip() != expected:
                return False
        return True

    @classmethod
    def _prepared_points(
        cls,
        entries: Sequence[Mapping[str, Any]],
        state: Mapping[str, Any],
        scope: VectorScope,
    ) -> list[PreparedVectorPoint]:
        points: list[PreparedVectorPoint] = []
        for entry in entries:
            if not cls._entry_matches_scope(entry, scope):
                raise QdrantSchemaError("vector_scope_conflict")
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
        source_path: str | Path | bytes,
        *,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
    ) -> MigrationPlan:
        raw, state, entries = self._load(source_path)
        digest = hashlib.sha256(raw).hexdigest()
        distance = str(state.get("distance") or "").strip().lower()
        scope_fingerprint = self._scope_fingerprint(scope)
        target_collection = self._store.collection_manager.target_collection_name(
            scope,
            index_version=digest,
        )
        if any(
            not self._entry_matches_scope(entry, scope)
            for entry in entries
        ):
            return MigrationPlan(
                status="blocked",
                reason="vector_scope_conflict",
                source_digest=digest,
                source_entries=len(entries),
                compatible_entries=0,
                target_collection=target_collection,
                dimensions=int(state.get("embedding_dimensions") or 0),
                conflicts=("source_entry_scope_mismatch",),
                distance=distance,
                scope_fingerprint=scope_fingerprint,
            )
        if str(state.get("schema") or "") not in {
            "codecompass_vector_index.v1",
            "codecompass_vector_index.v2",
        }:
            return MigrationPlan(
                status="blocked",
                reason="migration_required",
                source_digest=digest,
                source_entries=len(entries),
                compatible_entries=0,
                target_collection=target_collection,
                dimensions=int(state.get("embedding_dimensions") or 0),
                conflicts=("legacy_schema_missing_or_unknown",),
                distance=distance,
                scope_fingerprint=scope_fingerprint,
            )
        source_compatibility = self._source_compatibility(state)
        if source_compatibility is None:
            return MigrationPlan(
                status="blocked",
                reason="rebuild_required",
                source_digest=digest,
                source_entries=len(entries),
                compatible_entries=0,
                target_collection=target_collection,
                dimensions=int(state.get("embedding_dimensions") or 0),
                conflicts=("source_state_incomplete",),
                distance=distance,
                scope_fingerprint=scope_fingerprint,
            )
        report = compare_compatibility(compatibility, asdict(source_compatibility))
        if not report.compatible:
            return MigrationPlan(
                status="blocked",
                reason=report.reason,
                source_digest=digest,
                source_entries=len(entries),
                compatible_entries=0,
                target_collection=target_collection,
                dimensions=source_compatibility.dimensions,
                conflicts=(report.reason,),
                distance=source_compatibility.distance,
                scope_fingerprint=scope_fingerprint,
            )
        points = self._prepared_points(entries, state, scope)
        conflicts = () if len(points) == len(entries) else ("entries_without_vector_or_record_id",)
        status = "ready" if not conflicts else "blocked"
        reason = "migration_ready" if not conflicts else "rebuild_required"
        return MigrationPlan(
            status=status,
            reason=reason,
            source_digest=digest,
            source_entries=len(entries),
            compatible_entries=len(points),
            target_collection=target_collection,
            dimensions=source_compatibility.dimensions,
            conflicts=conflicts,
            distance=source_compatibility.distance,
            scope_fingerprint=scope_fingerprint,
        )

    def migrate(
        self,
        source_path: str | Path | bytes,
        *,
        scope: VectorScope,
        compatibility: CompatibilitySpec,
        checkpoint: MigrationCheckpoint | None = None,
        batch_size: int = 128,
        max_batches: int | None = None,
        idempotency_key: str = "",
    ) -> MigrationResult:
        started = time.monotonic()
        scope_fingerprint = self._scope_fingerprint(scope)
        normalized_idempotency_key = str(idempotency_key or "").strip()
        raw, state, entries = self._load(source_path)
        plan = self.dry_run(raw, scope=scope, compatibility=compatibility)
        if plan.status != "ready":
            result = IndexWriteResult(
                status="failed",
                mode="migrate",
                reason=plan.reason,
                indexed_documents=0,
                diagnostics={"status": "failed", "conflicts": plan.conflicts},
                failed=plan.source_entries,
                accepted=0,
            )
            self._observe(result, started)
            return MigrationResult(result, None, False)
        if (
            type(batch_size) is not int
            or not 1 <= batch_size <= 1000
        ):
            result = IndexWriteResult(
                status="failed",
                mode="migrate",
                reason="vector_batch_size_invalid",
                indexed_documents=0,
                diagnostics={
                    "status": "failed",
                    "reason": "vector_batch_size_invalid",
                },
                failed=plan.compatible_entries,
                accepted=0,
            )
            self._observe(result, started)
            return MigrationResult(result, None, False)
        if not normalized_idempotency_key:
            result = IndexWriteResult(
                status="failed",
                mode="migrate",
                reason="migration_idempotency_key_required",
                indexed_documents=0,
                diagnostics={
                    "status": "failed",
                    "reason": "migration_idempotency_key_required",
                },
                failed=plan.compatible_entries,
                accepted=0,
            )
            self._observe(result, started)
            return MigrationResult(result, None, False)
        idempotency_key_hash = hashlib.sha256(
            normalized_idempotency_key.encode("utf-8")
        ).hexdigest()
        points = self._prepared_points(entries, state, scope)
        if checkpoint is not None and (
            checkpoint.source_digest != plan.source_digest
            or not 0 <= checkpoint.next_offset <= len(points)
            or (
                checkpoint.scope_fingerprint != scope_fingerprint
                or checkpoint.idempotency_key_hash
                != idempotency_key_hash
            )
        ):
            result = IndexWriteResult(
                status="failed",
                mode="migrate",
                reason="migration_checkpoint_invalid",
                indexed_documents=0,
                diagnostics={"status": "failed"},
                failed=len(points),
                accepted=0,
            )
            self._observe(result, started)
            return MigrationResult(result, None, False)
        collection = self._store.prepare_collection(
            scope,
            compatibility,
            index_version=plan.source_digest,
        )
        offset = 0
        if checkpoint is not None:
            if checkpoint.collection_name != collection:
                result = IndexWriteResult(
                    status="failed",
                    mode="migrate",
                    reason="migration_checkpoint_invalid",
                    indexed_documents=0,
                    diagnostics={"status": "failed"},
                    failed=len(points),
                    accepted=0,
                )
                self._observe(result, started)
                return MigrationResult(result, None, False)
            offset = checkpoint.next_offset
        total_accepted = total_upserted = total_skipped = total_failed = 0
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
            total_accepted += write.accepted
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
                    accepted=total_accepted,
                )
                next_checkpoint = MigrationCheckpoint(
                    plan.source_digest,
                    collection,
                    offset,
                    scope_fingerprint,
                    idempotency_key_hash,
                )
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
                accepted=total_accepted,
            )
            next_checkpoint = MigrationCheckpoint(
                plan.source_digest,
                collection,
                offset,
                scope_fingerprint,
                idempotency_key_hash,
            )
            self._observe(result, started)
            return MigrationResult(result, next_checkpoint, False)
        try:
            self._store.activate_collection(scope, collection, compatibility)
        except (QdrantClientError, QdrantSchemaError) as exc:
            result = IndexWriteResult(
                status="partial",
                mode="migrate",
                reason=exc.reason,
                indexed_documents=total_upserted,
                diagnostics={
                    "status": "partial",
                    "next_offset": offset,
                    "source_preserved": True,
                },
                upserted=total_upserted,
                skipped=total_skipped,
                failed=0,
                accepted=total_accepted,
            )
            next_checkpoint = MigrationCheckpoint(
                plan.source_digest,
                collection,
                offset,
                scope_fingerprint,
                idempotency_key_hash,
            )
            self._observe(result, started)
            return MigrationResult(result, next_checkpoint, False)
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
            accepted=total_accepted,
        )
        self._observe(result, started)
        return MigrationResult(result, None, True)

    @staticmethod
    def _scope_fingerprint(scope: VectorScope) -> str:
        return VectorIndexArtifactLocator.scope_fingerprint(scope)

    def _observe(self, result: IndexWriteResult, started: float) -> None:
        emit_operation_observation(
            self._observer,
            operation="migrate",
            outcome=observation_outcome(result.status),
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={
                "accepted": result.accepted,
                "upserted": result.upserted,
                "skipped": result.skipped,
                "failed": result.failed,
            },
        )
