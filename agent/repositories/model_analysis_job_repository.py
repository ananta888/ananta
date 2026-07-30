"""Transactional SQLite persistence for Hub-owned model-analysis jobs."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from agent.services.model_analysis_job_service import (
    QUEUED_STATES,
    TERMINAL_STATES,
    ModelAnalysisJobRecord,
    ModelAnalysisJobServiceError,
    ModelAnalysisJobState,
    ModelAnalysisLimits,
)
from ananta_contracts.model_intelligence import AnalysisJob
from ananta_contracts.model_intelligence_execution import (
    AnalysisCompletion,
    ResourceLease,
)

_TERMINAL_VALUES = tuple(sorted(state.value for state in TERMINAL_STATES))
_QUEUED_VALUES = tuple(sorted(state.value for state in QUEUED_STATES))


class SQLiteModelAnalysisJobRepository:
    """Restart-safe repository with atomic admission and optimistic CAS."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS model_analysis_jobs (
            job_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN (
                    'submission_pending',
                    'queued',
                    'running',
                    'cancel_requested',
                    'succeeded',
                    'failed',
                    'cancelled'
                )
            ),
            version INTEGER NOT NULL CHECK (version >= 1),
            attempt INTEGER NOT NULL CHECK (attempt >= 0),
            lease_expires_epoch_ms INTEGER,
            projection_pending INTEGER NOT NULL CHECK (
                projection_pending IN (0, 1)
            ),
            idempotency_key_digest TEXT NOT NULL CHECK (
                length(idempotency_key_digest) = 64
            ),
            request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
            updated_epoch_ms INTEGER NOT NULL CHECK (updated_epoch_ms >= 0),
            record_json TEXT NOT NULL,
            UNIQUE (tenant_id, idempotency_key_digest)
        );

        CREATE INDEX IF NOT EXISTS ix_model_analysis_jobs_tenant_state
            ON model_analysis_jobs (tenant_id, state, updated_epoch_ms);

        CREATE INDEX IF NOT EXISTS ix_model_analysis_jobs_queue
            ON model_analysis_jobs (state, tenant_id, updated_epoch_ms);

        CREATE INDEX IF NOT EXISTS ix_model_analysis_jobs_recovery
            ON model_analysis_jobs (
                projection_pending,
                state,
                lease_expires_epoch_ms
            );
    """

    def __init__(self, database: str | Path) -> None:
        self._database = str(database)
        self._lock = threading.RLock()
        if self._database != ":memory:":
            path = Path(self._database)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as connection:
            connection.executescript(self._SCHEMA)
        if self._database != ":memory:":
            try:
                os.chmod(self._database, 0o600)
            except OSError:
                pass

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self._database,
            timeout=30,
            check_same_thread=False,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        if self._database != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def admit(
        self,
        record: ModelAnalysisJobRecord,
        *,
        idempotency_key_digest: str,
        request_digest: str,
        limits: ModelAnalysisLimits,
    ) -> tuple[ModelAnalysisJobRecord, bool]:
        self._validate_digest(
            idempotency_key_digest,
            "model_analysis_idempotency_digest_invalid",
        )
        self._validate_digest(
            request_digest,
            "model_analysis_request_digest_invalid",
        )
        with self._transaction() as connection:
            duplicate = connection.execute(
                """
                SELECT job_id, request_digest
                FROM model_analysis_jobs
                WHERE tenant_id = ? AND idempotency_key_digest = ?
                """,
                (record.job.tenant_id, idempotency_key_digest),
            ).fetchone()
            if duplicate is not None:
                if str(duplicate["request_digest"]) != request_digest:
                    raise ModelAnalysisJobServiceError(
                        "model_analysis_idempotency_conflict"
                    )
                stored = self._select_record(
                    connection,
                    str(duplicate["job_id"]),
                )
                if stored is None:
                    raise ModelAnalysisJobServiceError(
                        "model_analysis_repository_corrupt"
                    )
                return stored, False

            if self._select_record(connection, record.job.job_id) is not None:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_job_id_conflict"
                )

            global_queued = self._count_states(
                connection,
                states=_QUEUED_VALUES,
            )
            tenant_queued = self._count_states(
                connection,
                states=_QUEUED_VALUES,
                tenant_id=record.job.tenant_id,
            )
            tenant_active = self._count_not_states(
                connection,
                states=_TERMINAL_VALUES,
                tenant_id=record.job.tenant_id,
            )
            if global_queued >= limits.max_global_queued:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_global_queue_full",
                    retryable=True,
                )
            if tenant_queued >= limits.max_tenant_queued:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_tenant_queue_full",
                    retryable=True,
                )
            if tenant_active >= limits.max_tenant_active:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_tenant_active_limit",
                    retryable=True,
                )

            connection.execute(
                """
                INSERT INTO model_analysis_jobs (
                    job_id,
                    tenant_id,
                    state,
                    version,
                    attempt,
                    lease_expires_epoch_ms,
                    projection_pending,
                    idempotency_key_digest,
                    request_digest,
                    updated_epoch_ms,
                    record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._row_values(
                    record,
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                ),
            )
            return record, True

    def get(self, job_id: str) -> ModelAnalysisJobRecord | None:
        with self._connection() as connection:
            return self._select_record(connection, job_id)

    def compare_and_set(
        self,
        record: ModelAnalysisJobRecord,
        *,
        expected_version: int,
    ) -> ModelAnalysisJobRecord:
        if record.version != expected_version + 1:
            raise ModelAnalysisJobServiceError(
                "model_analysis_version_increment_invalid"
            )
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT record_json
                FROM model_analysis_jobs
                WHERE job_id = ? AND version = ?
                """,
                (record.job.job_id, expected_version),
            ).fetchone()
            if row is None:
                if self._select_record(connection, record.job.job_id) is None:
                    raise ModelAnalysisJobServiceError(
                        "model_analysis_job_not_found"
                    )
                raise ModelAnalysisJobServiceError(
                    "model_analysis_version_conflict",
                    retryable=True,
                )
            current = self._deserialize(str(row["record_json"]))
            if current.job.to_wire() != record.job.to_wire():
                raise ModelAnalysisJobServiceError(
                    "model_analysis_job_binding_immutable"
                )
            updated = connection.execute(
                """
                UPDATE model_analysis_jobs
                SET state = ?,
                    version = ?,
                    attempt = ?,
                    lease_expires_epoch_ms = ?,
                    projection_pending = ?,
                    updated_epoch_ms = ?,
                    record_json = ?
                WHERE job_id = ? AND version = ?
                """,
                (
                    record.state.value,
                    record.version,
                    record.attempt,
                    self._lease_expiry(record),
                    int(record.projection_pending),
                    record.updated_epoch_ms,
                    self._serialize(record),
                    record.job.job_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_version_conflict",
                    retryable=True,
                )
            return record

    def mark_projected(
        self,
        job_id: str,
        *,
        expected_version: int,
    ) -> ModelAnalysisJobRecord:
        with self._transaction() as connection:
            current = self._select_record(connection, job_id)
            if current is None:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_job_not_found"
                )
            if current.version != expected_version:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_version_conflict",
                    retryable=True,
                )
            projected = replace(current, projection_pending=False)
            updated = connection.execute(
                """
                UPDATE model_analysis_jobs
                SET projection_pending = 0, record_json = ?
                WHERE job_id = ? AND version = ?
                """,
                (self._serialize(projected), job_id, expected_version),
            )
            if updated.rowcount != 1:
                raise ModelAnalysisJobServiceError(
                    "model_analysis_version_conflict",
                    retryable=True,
                )
            return projected

    def list_recoverable(
        self,
        *,
        now_epoch_ms: int,
        limit: int,
    ) -> tuple[ModelAnalysisJobRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT record_json
                FROM model_analysis_jobs
                WHERE state = 'submission_pending'
                   OR projection_pending = 1
                   OR (
                        state IN ('running', 'cancel_requested')
                        AND lease_expires_epoch_ms IS NOT NULL
                        AND lease_expires_epoch_ms <= ?
                   )
                ORDER BY job_id
                LIMIT ?
                """,
                (now_epoch_ms, limit),
            ).fetchall()
            return tuple(
                self._deserialize(str(row["record_json"]))
                for row in rows
            )

    def list_page(
        self,
        *,
        tenant_id: str,
        after_job_id: str | None,
        limit: int,
    ) -> tuple[ModelAnalysisJobRecord, ...]:
        with self._connection() as connection:
            if after_job_id is None:
                rows = connection.execute(
                    """
                    SELECT record_json
                    FROM model_analysis_jobs
                    WHERE tenant_id = ?
                    ORDER BY job_id
                    LIMIT ?
                    """,
                    (tenant_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT record_json
                    FROM model_analysis_jobs
                    WHERE tenant_id = ? AND job_id > ?
                    ORDER BY job_id
                    LIMIT ?
                    """,
                    (tenant_id, after_job_id, limit),
                ).fetchall()
            return tuple(
                self._deserialize(str(row["record_json"]))
                for row in rows
            )

    @staticmethod
    def _select_record(
        connection: sqlite3.Connection,
        job_id: str,
    ) -> ModelAnalysisJobRecord | None:
        row = connection.execute(
            """
            SELECT record_json
            FROM model_analysis_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return SQLiteModelAnalysisJobRepository._deserialize(
            str(row["record_json"])
        )

    @staticmethod
    def _count_states(
        connection: sqlite3.Connection,
        *,
        states: tuple[str, ...],
        tenant_id: str | None = None,
    ) -> int:
        placeholders = ",".join("?" for _ in states)
        sql = (
            f"SELECT COUNT(*) AS count FROM model_analysis_jobs "
            f"WHERE state IN ({placeholders})"
        )
        values: tuple[object, ...] = states
        if tenant_id is not None:
            sql += " AND tenant_id = ?"
            values = (*values, tenant_id)
        row = connection.execute(sql, values).fetchone()
        return int(row["count"])

    @staticmethod
    def _count_not_states(
        connection: sqlite3.Connection,
        *,
        states: tuple[str, ...],
        tenant_id: str,
    ) -> int:
        placeholders = ",".join("?" for _ in states)
        row = connection.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM model_analysis_jobs
            WHERE tenant_id = ? AND state NOT IN ({placeholders})
            """,
            (tenant_id, *states),
        ).fetchone()
        return int(row["count"])

    @staticmethod
    def _row_values(
        record: ModelAnalysisJobRecord,
        *,
        idempotency_key_digest: str,
        request_digest: str,
    ) -> tuple[object, ...]:
        return (
            record.job.job_id,
            record.job.tenant_id,
            record.state.value,
            record.version,
            record.attempt,
            SQLiteModelAnalysisJobRepository._lease_expiry(record),
            int(record.projection_pending),
            idempotency_key_digest,
            request_digest,
            record.updated_epoch_ms,
            SQLiteModelAnalysisJobRepository._serialize(record),
        )

    @staticmethod
    def _lease_expiry(record: ModelAnalysisJobRecord) -> int | None:
        return (
            record.lease.expires_epoch_ms
            if record.lease is not None
            else None
        )

    @staticmethod
    def _serialize(record: ModelAnalysisJobRecord) -> str:
        return json.dumps(
            {
                "job": record.job.to_wire(),
                "state": record.state.value,
                "version": record.version,
                "attempt": record.attempt,
                "lease": (
                    record.lease.to_wire()
                    if record.lease is not None
                    else None
                ),
                "completion": (
                    record.completion.to_wire()
                    if record.completion is not None
                    else None
                ),
                "reason_code": record.reason_code,
                "projection_pending": record.projection_pending,
                "updated_epoch_ms": record.updated_epoch_ms,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _deserialize(payload: str) -> ModelAnalysisJobRecord:
        try:
            raw = json.loads(payload)
            return ModelAnalysisJobRecord(
                job=AnalysisJob.model_validate(raw["job"]),
                state=ModelAnalysisJobState(str(raw["state"])),
                version=int(raw["version"]),
                attempt=int(raw["attempt"]),
                lease=(
                    ResourceLease.model_validate(raw["lease"])
                    if raw.get("lease") is not None
                    else None
                ),
                completion=(
                    AnalysisCompletion.model_validate(raw["completion"])
                    if raw.get("completion") is not None
                    else None
                ),
                reason_code=str(raw["reason_code"]),
                projection_pending=bool(raw["projection_pending"]),
                updated_epoch_ms=int(raw["updated_epoch_ms"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelAnalysisJobServiceError(
                "model_analysis_repository_corrupt"
            ) from exc

    @staticmethod
    def _validate_digest(value: str, reason_code: str) -> None:
        if (
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ModelAnalysisJobServiceError(reason_code)


__all__ = ["SQLiteModelAnalysisJobRepository"]
