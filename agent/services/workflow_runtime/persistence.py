"""In-memory and SQLite persistence ports for canonical events/checkpoints."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Protocol

from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import FencingTokenError, OptimisticConcurrencyError
from agent.services.workflow_runtime.events import (
    CanonicalWorkflowEvent,
    EventStore,
    assert_workflow_event_dedupe_read_binding,
    workflow_event_dedupe_read_binding,
)
from agent.services.workflow_runtime.security import SignedCheckpoint


class CheckpointStore(Protocol):
    """Atomic state/checkpoint storage owned by the hub control plane."""

    def save(self, checkpoint: SignedCheckpoint, *, expected_revision: int) -> SignedCheckpoint: ...

    def get_latest(self, *, tenant_id: str, run_id: str, task_id: str) -> SignedCheckpoint | None: ...


class WorkflowCheckpointIdentityReadPort(Protocol):
    """Read one exact transition-owned checkpoint identity.

    The bounded canonical identity contract is additive and does not strengthen
    the existing broad ``CheckpointStore`` protocol.
    """

    def get_by_id(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        task_id: str,
        checkpoint_id: str,
    ) -> SignedCheckpoint | None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._history: dict[tuple[str, str, str], list[SignedCheckpoint]] = {}
        self._ids: dict[tuple[str, str], SignedCheckpoint] = {}
        self._lock = threading.RLock()

    def save(self, checkpoint: SignedCheckpoint, *, expected_revision: int) -> SignedCheckpoint:
        checkpoint._assert_structure()  # structurally safe; signature is verified by the caller's trust boundary
        key = (checkpoint.tenant_id, checkpoint.run_id, checkpoint.task_id)
        with self._lock:
            checkpoint_key = (checkpoint.tenant_id, checkpoint.checkpoint_id)
            duplicate = self._ids.get(checkpoint_key)
            if duplicate is not None:
                if canonical_json(duplicate.to_dict()) != canonical_json(checkpoint.to_dict()):
                    raise OptimisticConcurrencyError("checkpoint_id_payload_conflict")
                return _clone_checkpoint(duplicate)
            history = self._history.get(key, [])
            current_revision = history[-1].revision if history else 0
            current_fence = history[-1].fencing_token if history else 0
            if int(expected_revision) != current_revision or checkpoint.revision != current_revision + 1:
                raise OptimisticConcurrencyError(
                    f"checkpoint_revision_conflict:expected={expected_revision}:actual={current_revision}"
                )
            if checkpoint.fencing_token < current_fence:
                raise FencingTokenError("checkpoint_fencing_token_stale")
            stored = _clone_checkpoint(checkpoint)
            self._history.setdefault(key, []).append(stored)
            self._ids[checkpoint_key] = stored
            return _clone_checkpoint(stored)

    def get_latest(self, *, tenant_id: str, run_id: str, task_id: str) -> SignedCheckpoint | None:
        with self._lock:
            history = self._history.get((str(tenant_id), str(run_id), str(task_id)), [])
            return _clone_checkpoint(history[-1]) if history else None

    def get_by_id(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        task_id: str,
        checkpoint_id: str,
    ) -> SignedCheckpoint | None:
        tenant, workflow, run, task, identity = workflow_checkpoint_identity_read_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            task_id=task_id,
            checkpoint_id=checkpoint_id,
        )
        with self._lock:
            checkpoint = self._ids.get((tenant, identity))
            if checkpoint is None:
                return None
            assert_workflow_checkpoint_identity_read_binding(
                checkpoint,
                expected=(tenant, workflow, run, task, identity),
            )
            return _clone_checkpoint(checkpoint)

    def list_history(self, *, tenant_id: str, run_id: str, task_id: str) -> tuple[SignedCheckpoint, ...]:
        with self._lock:
            return tuple(
                _clone_checkpoint(value) for value in self._history.get((str(tenant_id), str(run_id), str(task_id)), ())
            )


class _SQLiteStore:
    def __init__(self, database: str | Path):
        self._database = str(database)
        self._connection = sqlite3.connect(self._database, timeout=30, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        if self._database != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._connection.commit()

    def _rollback(self) -> None:
        self._connection.rollback()


class SQLiteEventStore(_SQLiteStore, EventStore):
    """SQLite implementation with transactional sequence and dedupe decisions."""

    def __init__(self, database: str | Path):
        super().__init__(database)
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_runtime_events (
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    workflow_id TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id, run_id, sequence),
                    UNIQUE (tenant_id, run_id, dedupe_key),
                    UNIQUE (tenant_id, run_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS ix_workflow_runtime_events_run
                    ON workflow_runtime_events (tenant_id, run_id, sequence);
                """
            )

    def append(self, event: CanonicalWorkflowEvent, *, expected_sequence: int) -> CanonicalWorkflowEvent:
        event.assert_valid(allow_unsequenced=True)
        with self._lock:
            self._begin()
            try:
                duplicate = self._connection.execute(
                    """
                    SELECT event_json, content_hash FROM workflow_runtime_events
                    WHERE tenant_id = ? AND run_id = ? AND dedupe_key = ?
                    """,
                    (event.tenant_id, event.run_id, event.dedupe_key),
                ).fetchone()
                if duplicate is not None:
                    if str(duplicate["content_hash"]) != event.content_hash:
                        raise OptimisticConcurrencyError("dedupe_key_payload_conflict")
                    stored = CanonicalWorkflowEvent.from_mapping(json.loads(str(duplicate["event_json"])))
                    self._commit()
                    return stored
                row = self._connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) AS current_sequence
                    FROM workflow_runtime_events WHERE tenant_id = ? AND run_id = ?
                    """,
                    (event.tenant_id, event.run_id),
                ).fetchone()
                current = int(row["current_sequence"] if row else 0)
                if int(expected_sequence) != current:
                    raise OptimisticConcurrencyError(
                        f"event_sequence_conflict:expected={expected_sequence}:actual={current}"
                    )
                stored = event.with_sequence(current + 1)
                self._connection.execute(
                    """
                    INSERT INTO workflow_runtime_events
                    (tenant_id, run_id, sequence, workflow_id, dedupe_key, event_id,
                     content_hash, event_json, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stored.tenant_id,
                        stored.run_id,
                        stored.sequence,
                        stored.workflow_id,
                        stored.dedupe_key,
                        stored.event_id,
                        stored.content_hash,
                        canonical_json(stored.to_dict()),
                        stored.occurred_at,
                    ),
                )
                self._commit()
                return stored
            except Exception:
                self._rollback()
                raise

    def list_events(
        self,
        *,
        tenant_id: str,
        run_id: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[CanonicalWorkflowEvent, ...]:
        validated_tenant_id = require_canonical_identity(
            tenant_id,
            field_name="tenant_id",
        )
        validated_run_id = require_canonical_identity(
            run_id,
            field_name="run_id",
        )
        query = """
            SELECT event_json FROM workflow_runtime_events
            WHERE tenant_id = ? AND run_id = ? AND sequence > ?
            ORDER BY sequence ASC
        """
        parameters: list[object] = [
            validated_tenant_id,
            validated_run_id,
            int(after_sequence),
        ]
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(max(0, int(limit)))
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return tuple(CanonicalWorkflowEvent.from_mapping(json.loads(str(row["event_json"]))) for row in rows)

    def get_by_dedupe(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        dedupe_key: str,
    ) -> CanonicalWorkflowEvent | None:
        tenant, workflow, run, dedupe = workflow_event_dedupe_read_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            dedupe_key=dedupe_key,
        )
        with self._lock:
            row = self._connection.execute(
                """
                SELECT event_json FROM workflow_runtime_events
                WHERE tenant_id = ? AND run_id = ? AND dedupe_key = ?
                """,
                (tenant, run, dedupe),
            ).fetchone()
        if row is None:
            return None
        event = CanonicalWorkflowEvent.from_mapping(json.loads(str(row["event_json"])))
        assert_workflow_event_dedupe_read_binding(
            event,
            expected=(tenant, workflow, run, dedupe),
        )
        return event


class SQLiteCheckpointStore(_SQLiteStore, CheckpointStore):
    """Durable RPO-0 checkpoint history with revision and fencing checks."""

    def __init__(self, database: str | Path):
        super().__init__(database)
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_runtime_checkpoints (
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id, run_id, task_id, revision),
                    UNIQUE (tenant_id, checkpoint_id)
                );
                CREATE INDEX IF NOT EXISTS ix_workflow_runtime_checkpoints_latest
                    ON workflow_runtime_checkpoints (tenant_id, run_id, task_id, revision DESC);
                """
            )

    def save(self, checkpoint: SignedCheckpoint, *, expected_revision: int) -> SignedCheckpoint:
        checkpoint._assert_structure()
        with self._lock:
            self._begin()
            try:
                duplicate = self._connection.execute(
                    """
                    SELECT checkpoint_json FROM workflow_runtime_checkpoints
                    WHERE tenant_id = ? AND checkpoint_id = ?
                    """,
                    (checkpoint.tenant_id, checkpoint.checkpoint_id),
                ).fetchone()
                if duplicate is not None:
                    stored = SignedCheckpoint.from_mapping(json.loads(str(duplicate["checkpoint_json"])))
                    if canonical_json(stored.to_dict()) != canonical_json(checkpoint.to_dict()):
                        raise OptimisticConcurrencyError("checkpoint_id_payload_conflict")
                    self._commit()
                    return stored
                latest = self._connection.execute(
                    """
                    SELECT revision, fencing_token FROM workflow_runtime_checkpoints
                    WHERE tenant_id = ? AND run_id = ? AND task_id = ?
                    ORDER BY revision DESC LIMIT 1
                    """,
                    (checkpoint.tenant_id, checkpoint.run_id, checkpoint.task_id),
                ).fetchone()
                current_revision = int(latest["revision"] if latest else 0)
                current_fence = int(latest["fencing_token"] if latest else 0)
                if int(expected_revision) != current_revision or checkpoint.revision != current_revision + 1:
                    raise OptimisticConcurrencyError(
                        f"checkpoint_revision_conflict:expected={expected_revision}:actual={current_revision}"
                    )
                if checkpoint.fencing_token < current_fence:
                    raise FencingTokenError("checkpoint_fencing_token_stale")
                self._connection.execute(
                    """
                    INSERT INTO workflow_runtime_checkpoints
                    (tenant_id, run_id, task_id, revision, fencing_token,
                     checkpoint_id, checkpoint_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        checkpoint.tenant_id,
                        checkpoint.run_id,
                        checkpoint.task_id,
                        checkpoint.revision,
                        checkpoint.fencing_token,
                        checkpoint.checkpoint_id,
                        canonical_json(checkpoint.to_dict()),
                        checkpoint.created_at,
                    ),
                )
                self._commit()
                return checkpoint
            except Exception:
                self._rollback()
                raise

    def get_latest(self, *, tenant_id: str, run_id: str, task_id: str) -> SignedCheckpoint | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT checkpoint_json FROM workflow_runtime_checkpoints
                WHERE tenant_id = ? AND run_id = ? AND task_id = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (str(tenant_id), str(run_id), str(task_id)),
            ).fetchone()
        if row is None:
            return None
        return SignedCheckpoint.from_mapping(json.loads(str(row["checkpoint_json"])))

    def get_by_id(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        task_id: str,
        checkpoint_id: str,
    ) -> SignedCheckpoint | None:
        tenant, workflow, run, task, identity = workflow_checkpoint_identity_read_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            task_id=task_id,
            checkpoint_id=checkpoint_id,
        )
        with self._lock:
            row = self._connection.execute(
                """
                SELECT checkpoint_json FROM workflow_runtime_checkpoints
                WHERE tenant_id = ? AND checkpoint_id = ?
                """,
                (tenant, identity),
            ).fetchone()
        if row is None:
            return None
        checkpoint = SignedCheckpoint.from_mapping(json.loads(str(row["checkpoint_json"])))
        assert_workflow_checkpoint_identity_read_binding(
            checkpoint,
            expected=(tenant, workflow, run, task, identity),
        )
        return checkpoint

    def list_history(self, *, tenant_id: str, run_id: str, task_id: str) -> tuple[SignedCheckpoint, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT checkpoint_json FROM workflow_runtime_checkpoints
                WHERE tenant_id = ? AND run_id = ? AND task_id = ?
                ORDER BY revision ASC
                """,
                (str(tenant_id), str(run_id), str(task_id)),
            ).fetchall()
        return tuple(SignedCheckpoint.from_mapping(json.loads(str(row["checkpoint_json"]))) for row in rows)


def _clone_checkpoint(checkpoint: SignedCheckpoint) -> SignedCheckpoint:
    return SignedCheckpoint.from_mapping(checkpoint.to_dict())


def workflow_checkpoint_identity_read_binding(
    *,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    task_id: str,
    checkpoint_id: str,
) -> tuple[str, str, str, str, str]:
    tenant = require_canonical_identity(tenant_id, field_name="tenant_id")
    workflow = require_canonical_identity(
        workflow_id,
        field_name="workflow_id",
    )
    run = require_canonical_identity(run_id, field_name="run_id")
    task = _checkpoint_read_identifier(task_id, reason="task_id")
    identity = _checkpoint_read_identifier(
        checkpoint_id,
        reason="checkpoint_id",
    )
    return tenant, workflow, run, task, identity


def _checkpoint_read_identifier(value: str, *, reason: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256 or "\x00" in value:
        raise ValueError(f"workflow_checkpoint_{reason}_invalid")
    return value


def assert_workflow_checkpoint_identity_read_binding(
    checkpoint: SignedCheckpoint,
    *,
    expected: tuple[str, str, str, str, str],
) -> None:
    actual = (
        checkpoint.tenant_id,
        checkpoint.workflow_id,
        checkpoint.run_id,
        checkpoint.task_id,
        checkpoint.checkpoint_id,
    )
    if actual != expected:
        raise OptimisticConcurrencyError("workflow_checkpoint_id_binding_conflict")
