"""Recipient-side lease validation ports and same-transaction SQL readers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Protocol

import sqlalchemy as sa

from agent.db_models.workflow_runtime import WorkflowRuntimeCheckpointDB
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError
from agent.services.workflow_runtime.security import SignedCheckpoint


class CheckpointLeaseFence(Protocol):
    """Verify an authority row supplied by the recipient's own transaction.

    Implementations must not read a second store/connection to decide whether
    the lease is current; that would reintroduce a check/write race.
    """

    checkpoint: SignedCheckpoint

    def assert_current(
        self,
        current: SignedCheckpoint | None,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
    ) -> None: ...


@dataclass(frozen=True)
class OwnershipLeaseRecipient:
    tenant_id: str
    workflow_id: str
    run_id: str


def ownership_lease_recipient(values, lease):
    return OwnershipLeaseRecipient(values["tenant_id"], lease.checkpoint.workflow_id, values["run_id"])


def validate_sqlite_lease(connection, lease: CheckpointLeaseFence, recipient) -> None:
    """Caller must hold BEGIN IMMEDIATE through its own write and commit."""
    binding = lease.checkpoint
    try:
        row = connection.execute(
            "SELECT checkpoint_json FROM workflow_runtime_checkpoints "
            "WHERE tenant_id = ? AND run_id = ? AND task_id = ? ORDER BY revision DESC LIMIT 1",
            (binding.tenant_id, binding.run_id, binding.task_id),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise OptimisticConcurrencyError("bpmn_recipient_lease_authority_unavailable") from exc
    current = SignedCheckpoint.from_mapping(json.loads(row[0])) if row else None
    lease.assert_current(
        current,
        tenant_id=recipient.tenant_id,
        workflow_id=recipient.workflow_id,
        run_id=recipient.run_id,
    )


def lock_sqlalchemy_lease_anchor(session, binding: SignedCheckpoint) -> bool:
    """Serialize every revision writer/recipient on the first immutable row.

    A no-op UPDATE takes a database write lock on SQLite and a row lock on
    PostgreSQL. It changes no signed bytes/revision. Locking only the newest
    append-only row would leave later revisions as unlocked phantoms.
    """
    row = WorkflowRuntimeCheckpointDB
    result = session.execute(
        sa.update(row)
        .where(
            row.tenant_id == binding.tenant_id,
            row.run_id == binding.run_id,
            row.task_id == binding.task_id,
            row.revision == 1,
        )
        .values(revision=row.revision)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def validate_sqlalchemy_lease(session, lease: CheckpointLeaseFence, recipient) -> None:
    """Lock and validate in the transaction that will commit the mutation.

    The caller owns commit/rollback. No lock or signature comparison makes a
    subsequent write through another session/store atomic with this check.
    """
    binding = lease.checkpoint
    if not lock_sqlalchemy_lease_anchor(session, binding):
        raise OptimisticConcurrencyError("bpmn_recipient_lease_authority_unavailable")
    payload = session.execute(
        sa.select(WorkflowRuntimeCheckpointDB.signed_checkpoint)
        .where(
            WorkflowRuntimeCheckpointDB.tenant_id == binding.tenant_id,
            WorkflowRuntimeCheckpointDB.run_id == binding.run_id,
            WorkflowRuntimeCheckpointDB.task_id == binding.task_id,
        )
        .order_by(WorkflowRuntimeCheckpointDB.revision.desc())
        .limit(1)
    ).scalar_one_or_none()
    # Read the actual column, not a possibly cached ORM identity-map instance.
    current = SignedCheckpoint.from_mapping(dict(payload)) if payload is not None else None
    lease.assert_current(
        current,
        tenant_id=recipient.tenant_id,
        workflow_id=recipient.workflow_id,
        run_id=recipient.run_id,
    )
