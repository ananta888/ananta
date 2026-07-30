"""SQLite persistence adapter for model-intelligence retention state.

The adapter stores only canonical ``ArtifactRef`` payloads and content-free
state transitions. It never resolves an artifact to a filesystem path and does
not perform deletion itself.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ananta_contracts.model_intelligence import ArtifactRef
from agent.services.model_intelligence_security_policy import (
    ModelIntelligenceRetentionDecision,
    ModelIntelligenceRetentionRecord,
    RetentionClass,
    RetentionState,
)


class ModelIntelligenceRetentionStoreError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class PersistedRetentionTransition:
    transition_id: str
    artifact_id: str
    previous_state: RetentionState
    next_state: RetentionState
    reason_code: str
    idempotency_digest: str
    recorded_at_epoch_seconds: int


@runtime_checkable
class ModelIntelligenceRetentionStorePort(Protocol):
    def register(self, record: ModelIntelligenceRetentionRecord) -> None: ...

    def get(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
    ) -> ModelIntelligenceRetentionRecord | None: ...

    def apply(
        self,
        decision: ModelIntelligenceRetentionDecision,
        *,
        tenant_id: str,
        recorded_at_epoch_seconds: int,
    ) -> ModelIntelligenceRetentionRecord: ...


class SqliteModelIntelligenceRetentionAdapter:
    """Transactional, tenant-bound retention state with replay-safe transitions."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        if path.exists() and path.is_symlink():
            raise ModelIntelligenceRetentionStoreError("retention_database_symlink_forbidden")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_intelligence_retention (
                    artifact_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    artifact_json TEXT NOT NULL,
                    retention_class TEXT NOT NULL,
                    created_at_epoch_seconds INTEGER NOT NULL,
                    retain_until_epoch_seconds INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_model_intelligence_retention_tenant
                    ON model_intelligence_retention (tenant_id, state);
                CREATE TABLE IF NOT EXISTS model_intelligence_retention_transition (
                    transition_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    previous_state TEXT NOT NULL,
                    next_state TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    idempotency_digest TEXT NOT NULL,
                    recorded_at_epoch_seconds INTEGER NOT NULL,
                    FOREIGN KEY (artifact_id)
                        REFERENCES model_intelligence_retention (artifact_id)
                );
                CREATE INDEX IF NOT EXISTS idx_model_intelligence_transition_artifact
                    ON model_intelligence_retention_transition
                    (tenant_id, artifact_id, recorded_at_epoch_seconds);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _artifact_json(artifact_ref: ArtifactRef) -> str:
        return json.dumps(
            artifact_ref.to_wire(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> ModelIntelligenceRetentionRecord:
        return ModelIntelligenceRetentionRecord(
            tenant_id=str(row["tenant_id"]),
            artifact_ref=ArtifactRef.model_validate_json(str(row["artifact_json"])),
            retention_class=RetentionClass(str(row["retention_class"])),
            created_at_epoch_seconds=int(row["created_at_epoch_seconds"]),
            retain_until_epoch_seconds=int(row["retain_until_epoch_seconds"]),
            state=RetentionState(str(row["state"])),
        )

    def register(self, record: ModelIntelligenceRetentionRecord) -> None:
        artifact_json = self._artifact_json(record.artifact_ref)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT tenant_id, artifact_json, retention_class,
                       created_at_epoch_seconds, retain_until_epoch_seconds, state
                FROM model_intelligence_retention
                WHERE artifact_id = ?
                """,
                (record.artifact_ref.artifact_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["tenant_id"]) != record.tenant_id:
                    raise ModelIntelligenceRetentionStoreError("tenant_scope_mismatch")
                expected = (
                    artifact_json,
                    record.retention_class.value,
                    record.created_at_epoch_seconds,
                    record.retain_until_epoch_seconds,
                    record.state.value,
                )
                observed = (
                    str(existing["artifact_json"]),
                    str(existing["retention_class"]),
                    int(existing["created_at_epoch_seconds"]),
                    int(existing["retain_until_epoch_seconds"]),
                    str(existing["state"]),
                )
                if observed != expected:
                    raise ModelIntelligenceRetentionStoreError("retention_record_conflict")
                return
            connection.execute(
                """
                INSERT INTO model_intelligence_retention (
                    artifact_id, tenant_id, artifact_json, retention_class,
                    created_at_epoch_seconds, retain_until_epoch_seconds, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.artifact_ref.artifact_id,
                    record.tenant_id,
                    artifact_json,
                    record.retention_class.value,
                    record.created_at_epoch_seconds,
                    record.retain_until_epoch_seconds,
                    record.state.value,
                ),
            )

    def get(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
    ) -> ModelIntelligenceRetentionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, artifact_json, retention_class,
                       created_at_epoch_seconds, retain_until_epoch_seconds, state
                FROM model_intelligence_retention
                WHERE tenant_id = ? AND artifact_id = ?
                """,
                (str(tenant_id), str(artifact_id)),
            ).fetchone()
        return None if row is None else self._record(row)

    def apply(
        self,
        decision: ModelIntelligenceRetentionDecision,
        *,
        tenant_id: str,
        recorded_at_epoch_seconds: int,
    ) -> ModelIntelligenceRetentionRecord:
        if not decision.allowed:
            raise ModelIntelligenceRetentionStoreError("retention_decision_denied")
        if isinstance(recorded_at_epoch_seconds, bool) or recorded_at_epoch_seconds < 0:
            raise ModelIntelligenceRetentionStoreError("retention_transition_time_invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute(
                """
                SELECT artifact_id FROM model_intelligence_retention_transition
                WHERE transition_id = ?
                """,
                (decision.transition_id,),
            ).fetchone()
            row = connection.execute(
                """
                SELECT tenant_id, artifact_json, retention_class,
                       created_at_epoch_seconds, retain_until_epoch_seconds, state
                FROM model_intelligence_retention
                WHERE artifact_id = ?
                """,
                (decision.artifact_ref.artifact_id,),
            ).fetchone()
            if row is None:
                raise ModelIntelligenceRetentionStoreError("retention_record_not_found")
            if str(row["tenant_id"]) != str(tenant_id):
                raise ModelIntelligenceRetentionStoreError("tenant_scope_mismatch")
            if ArtifactRef.model_validate_json(str(row["artifact_json"])) != decision.artifact_ref:
                raise ModelIntelligenceRetentionStoreError("retention_artifact_ref_mismatch")
            if replay is not None:
                return self._record(row)
            if str(row["state"]) != decision.previous_state.value:
                raise ModelIntelligenceRetentionStoreError("retention_state_conflict")
            connection.execute(
                """
                UPDATE model_intelligence_retention
                SET state = ?, revision = revision + 1
                WHERE artifact_id = ? AND tenant_id = ?
                """,
                (
                    decision.next_state.value,
                    decision.artifact_ref.artifact_id,
                    str(tenant_id),
                ),
            )
            connection.execute(
                """
                INSERT INTO model_intelligence_retention_transition (
                    transition_id, tenant_id, artifact_id, previous_state,
                    next_state, reason_code, idempotency_digest,
                    recorded_at_epoch_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.transition_id,
                    str(tenant_id),
                    decision.artifact_ref.artifact_id,
                    decision.previous_state.value,
                    decision.next_state.value,
                    decision.reason_code,
                    decision.idempotency_digest,
                    recorded_at_epoch_seconds,
                ),
            )
            updated = connection.execute(
                """
                SELECT tenant_id, artifact_json, retention_class,
                       created_at_epoch_seconds, retain_until_epoch_seconds, state
                FROM model_intelligence_retention
                WHERE artifact_id = ?
                """,
                (decision.artifact_ref.artifact_id,),
            ).fetchone()
        assert updated is not None
        return self._record(updated)

    def history(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
    ) -> tuple[PersistedRetentionTransition, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT transition_id, artifact_id, previous_state, next_state,
                       reason_code, idempotency_digest, recorded_at_epoch_seconds
                FROM model_intelligence_retention_transition
                WHERE tenant_id = ? AND artifact_id = ?
                ORDER BY recorded_at_epoch_seconds, transition_id
                """,
                (str(tenant_id), str(artifact_id)),
            ).fetchall()
        return tuple(
            PersistedRetentionTransition(
                transition_id=str(row["transition_id"]),
                artifact_id=str(row["artifact_id"]),
                previous_state=RetentionState(str(row["previous_state"])),
                next_state=RetentionState(str(row["next_state"])),
                reason_code=str(row["reason_code"]),
                idempotency_digest=str(row["idempotency_digest"]),
                recorded_at_epoch_seconds=int(row["recorded_at_epoch_seconds"]),
            )
            for row in rows
        )


__all__ = [
    "ModelIntelligenceRetentionStoreError",
    "ModelIntelligenceRetentionStorePort",
    "PersistedRetentionTransition",
    "SqliteModelIntelligenceRetentionAdapter",
]
