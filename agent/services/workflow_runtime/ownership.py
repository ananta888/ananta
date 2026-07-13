"""Hub-owned run/step leases, fencing, acknowledgements, and retry budgets.

Every worker/runtime claim receives a unique attempt ID and monotonically
increasing fencing token. Tool/native runtimes pass that fencing token to the
side-effect ledger and checkpoint store. Stale heartbeats, results, failures, and
side-effect completions fail closed.

The retry budget is shared across categories (hub task, runtime, tool, provider,
or Temporal activity). ``retry_id`` makes delivery idempotent while ``used`` is a
single combined counter, preventing nested runtimes from multiplying retries.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    InvalidTransitionError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent
from ananta_contracts.hub_task_gateway import RETRY_CATEGORIES

EXECUTION_OWNERSHIP_SCHEMA = "ananta.execution_ownership.v1"
RETRY_BUDGET_SCHEMA = "ananta.retry_budget.v1"
OWNERSHIP_STATUSES = frozenset({"active", "completed", "failed", "orphaned", "dead_letter"})


def ownership_event(
    ownership: "ExecutionOwnership",
    *,
    correlation_id: str,
    causation_id: str,
    actor: str = "hub",
) -> CanonicalWorkflowEvent:
    """Map an atomically committed lease revision to a canonical event."""

    event_suffix = {
        "active": "ownership_claimed",
        "completed": "result_acknowledged",
        "failed": "attempt_failed",
        "orphaned": "orphaned",
        "dead_letter": "dead_lettered",
    }[ownership.status]
    return CanonicalWorkflowEvent.build(
        tenant_id=ownership.tenant_id,
        workflow_id=ownership.workflow_id,
        run_id=ownership.run_id,
        step_id=ownership.step_id,
        attempt=ownership.fencing_token,
        event_type=f"workflow.step.{event_suffix}",
        correlation_id=correlation_id,
        causation_id=causation_id,
        dedupe_key=(
            f"ownership:{ownership.run_id}:{ownership.step_id}:"
            f"{ownership.attempt_id}:{ownership.revision}"
        ),
        actor=actor,
        payload={
            "attempt_id": ownership.attempt_id,
            "owner_id": ownership.owner_id,
            "fencing_token": ownership.fencing_token,
            "lease_expires_at": ownership.lease_expires_at,
            "result_ack_key": ownership.result_ack_key,
            "failure_code": ownership.failure_code,
        },
        occurred_at=ownership.last_heartbeat_at,
        event_id=(
            f"wfe-ownership-{ownership.run_id}-{ownership.step_id}-"
            f"{ownership.attempt_id}-{ownership.revision}"
        ),
    )


@dataclass(frozen=True)
class ExecutionOwnership:
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    attempt_id: str
    owner_id: str
    fencing_token: int
    revision: int
    status: str
    lease_expires_at: float
    last_heartbeat_at: float
    result_ack_key: str = ""
    failure_code: str = ""
    schema: str = EXECUTION_OWNERSHIP_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.attempt_id,
            self.owner_id,
        )
        if any(not value for value in required):
            raise ValueError("execution_ownership_binding_required")
        if self.status not in OWNERSHIP_STATUSES:
            raise ValueError("execution_ownership_status_invalid")
        if self.fencing_token < 1 or self.revision < 1:
            raise ValueError("execution_ownership_fencing_invalid")
        if self.schema != EXECUTION_OWNERSHIP_SCHEMA:
            raise ValueError("execution_ownership_schema_unsupported")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "ExecutionOwnership":
        value = cls(
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            attempt_id=str(raw.get("attempt_id") or ""),
            owner_id=str(raw.get("owner_id") or ""),
            fencing_token=int(raw.get("fencing_token") or 0),
            revision=int(raw.get("revision") or 0),
            status=str(raw.get("status") or ""),
            lease_expires_at=float(raw.get("lease_expires_at") or 0),
            last_heartbeat_at=float(raw.get("last_heartbeat_at") or 0),
            result_ack_key=str(raw.get("result_ack_key") or ""),
            failure_code=str(raw.get("failure_code") or ""),
            schema=str(raw.get("schema") or EXECUTION_OWNERSHIP_SCHEMA),
        )
        value.assert_valid()
        return value

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "owner_id": self.owner_id,
            "fencing_token": self.fencing_token,
            "revision": self.revision,
            "status": self.status,
            "lease_expires_at": self.lease_expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "result_ack_key": self.result_ack_key,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True)
class OwnershipClaim:
    ownership: ExecutionOwnership
    acquired: bool
    reason: str


@dataclass(frozen=True)
class RetryBudgetSnapshot:
    tenant_id: str
    run_id: str
    used: int
    maximum: int
    schema: str = RETRY_BUDGET_SCHEMA

    @property
    def remaining(self) -> int:
        return max(0, self.maximum - self.used)


class RetryBudgetOwner(Protocol):
    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot:
        ...

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot:
        ...


class ExecutionOwnershipStore(RetryBudgetOwner, Protocol):
    def claim(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        now: float | None = None,
    ) -> OwnershipClaim:
        ...

    def heartbeat(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        lease_seconds: float,
        now: float | None = None,
    ) -> ExecutionOwnership:
        ...

    def acknowledge_result(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        result_ack_key: str,
        now: float | None = None,
    ) -> ExecutionOwnership:
        ...

    def fail_attempt(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        expected_revision: int,
        failure_code: str,
        dead_letter: bool = False,
        now: float | None = None,
    ) -> ExecutionOwnership:
        ...

    def reconcile_orphan(
        self,
        *,
        tenant_id: str,
        run_id: str,
        step_id: str,
        now: float | None = None,
    ) -> ExecutionOwnership | None:
        ...

    def get(
        self, *, tenant_id: str, run_id: str, step_id: str
    ) -> ExecutionOwnership | None:
        ...


class InMemoryExecutionOwnershipStore:
    def __init__(self) -> None:
        self._current: dict[tuple[str, str, str], ExecutionOwnership] = {}
        self._history: dict[tuple[str, str, str], list[ExecutionOwnership]] = {}
        self._retry_used: dict[tuple[str, str], int] = {}
        self._retry_maximum: dict[tuple[str, str], int] = {}
        self._retry_ids: dict[tuple[str, str, str], str] = {}
        self._lock = threading.RLock()

    def claim(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        now: float | None = None,
    ) -> OwnershipClaim:
        timestamp = _validate_lease(lease_seconds, now)
        key = (str(tenant_id), str(run_id), str(step_id))
        with self._lock:
            current = self._current.get(key)
            if current is not None and current.workflow_id != str(workflow_id):
                raise OptimisticConcurrencyError("execution_ownership_workflow_binding_conflict")
            if current and current.status == "completed":
                return OwnershipClaim(current, False, "already_completed")
            if current and current.status == "active" and current.lease_expires_at > timestamp:
                reason = "already_owned" if current.owner_id == str(owner_id) else "lease_held"
                return OwnershipClaim(current, False, reason)
            next_fence = (current.fencing_token + 1) if current else 1
            attempt_id = f"att-{uuid.uuid4().hex}"
            if current is not None:
                self.consume_retry(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    retry_id=attempt_id,
                    category="hub_task",
                    maximum=maximum_retries,
                )
            ownership = ExecutionOwnership(
                tenant_id=str(tenant_id),
                workflow_id=str(workflow_id),
                run_id=str(run_id),
                step_id=str(step_id),
                attempt_id=attempt_id,
                owner_id=str(owner_id),
                fencing_token=next_fence,
                revision=(current.revision + 1) if current else 1,
                status="active",
                lease_expires_at=timestamp + float(lease_seconds),
                last_heartbeat_at=timestamp,
            )
            ownership.assert_valid()
            self._save(key, ownership)
            return OwnershipClaim(ownership, True, "acquired" if current is None else "recovered")

    def heartbeat(self, **values: Any) -> ExecutionOwnership:
        timestamp = _validate_lease(float(values["lease_seconds"]), values.get("now"))
        with self._lock:
            key, current = self._owned(values)
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise FencingTokenError("heartbeat_owner_no_longer_active")
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("ownership_lease_expired")
            updated = replace(
                current,
                revision=current.revision + 1,
                last_heartbeat_at=timestamp,
                lease_expires_at=timestamp + float(values["lease_seconds"]),
            )
            self._save(key, updated)
            return updated

    def acknowledge_result(self, **values: Any) -> ExecutionOwnership:
        result_ack_key = str(values.get("result_ack_key") or "")
        if not result_ack_key:
            raise ValueError("result_ack_key_required")
        timestamp = _timestamp(values.get("now"))
        with self._lock:
            key, current = self._owned(values)
            if current.status == "completed" and current.result_ack_key == result_ack_key:
                return current
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active" or current.lease_expires_at <= timestamp:
                raise FencingTokenError("result_owner_not_active")
            updated = replace(
                current,
                revision=current.revision + 1,
                status="completed",
                result_ack_key=result_ack_key,
                lease_expires_at=timestamp,
            )
            self._save(key, updated)
            return updated

    def fail_attempt(self, *, failure_code: str, dead_letter: bool = False, **values: Any) -> ExecutionOwnership:
        with self._lock:
            key, current = self._owned(values)
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise InvalidTransitionError("failure_requires_active_ownership")
            timestamp = _timestamp(values.get("now"))
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("failure_owner_lease_expired")
            updated = replace(
                current,
                revision=current.revision + 1,
                status="dead_letter" if dead_letter else "failed",
                failure_code=str(failure_code or "execution_failed"),
                lease_expires_at=timestamp,
            )
            self._save(key, updated)
            return updated

    def reconcile_orphan(
        self, *, tenant_id: str, run_id: str, step_id: str, now: float | None = None
    ) -> ExecutionOwnership | None:
        timestamp = float(now if now is not None else time.time())
        key = (str(tenant_id), str(run_id), str(step_id))
        with self._lock:
            current = self._current.get(key)
            if current is None or current.status != "active" or current.lease_expires_at > timestamp:
                return None
            updated = replace(
                current,
                revision=current.revision + 1,
                status="orphaned",
                failure_code="lease_expired",
            )
            self._save(key, updated)
            return updated

    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        with self._lock:
            return self._current.get((str(tenant_id), str(run_id), str(step_id)))

    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot:
        if maximum < 0 or not retry_id or category not in RETRY_CATEGORIES:
            raise ValueError("retry_budget_input_invalid")
        key = (str(tenant_id), str(run_id))
        dedupe = (*key, str(retry_id))
        with self._lock:
            current = self._retry_used.get(key, 0)
            configured_maximum = self._retry_maximum.get(key)
            if configured_maximum is not None and configured_maximum != int(maximum):
                raise InvalidTransitionError("retry_budget_maximum_mismatch")
            existing_category = self._retry_ids.get(dedupe)
            if existing_category is not None and existing_category != str(category):
                raise InvalidTransitionError("retry_budget_retry_id_binding_mismatch")
            if existing_category is not None:
                return RetryBudgetSnapshot(*key, used=current, maximum=int(maximum))
            if current >= int(maximum):
                raise InvalidTransitionError("retry_budget_exhausted")
            self._retry_maximum[key] = int(maximum)
            self._retry_ids[dedupe] = str(category)
            self._retry_used[key] = current + 1
            return RetryBudgetSnapshot(*key, used=current + 1, maximum=int(maximum))

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot:
        with self._lock:
            used = self._retry_used.get((str(tenant_id), str(run_id)), 0)
            configured_maximum = self._retry_maximum.get((str(tenant_id), str(run_id)))
            if configured_maximum is not None and configured_maximum != int(maximum):
                raise InvalidTransitionError("retry_budget_maximum_mismatch")
        return RetryBudgetSnapshot(str(tenant_id), str(run_id), used=used, maximum=int(maximum))

    def _owned(self, values: dict[str, Any]) -> tuple[tuple[str, str, str], ExecutionOwnership]:
        key = (str(values["tenant_id"]), str(values["run_id"]), str(values["step_id"]))
        current = self._current.get(key)
        if current is None:
            raise KeyError("execution_ownership_not_found")
        _assert_owner(
            current,
            attempt_id=str(values["attempt_id"]),
            owner_id=str(values["owner_id"]),
            fencing_token=int(values["fencing_token"]),
        )
        return key, current

    def _save(self, key: tuple[str, str, str], value: ExecutionOwnership) -> None:
        value.assert_valid()
        self._current[key] = value
        self._history.setdefault(key, []).append(value)


class SQLiteExecutionOwnershipStore:
    """SQLite lease store; ownership and combined retry budget mutate atomically."""

    def __init__(self, database: str | Path):
        self._connection = sqlite3.connect(str(database), timeout=30, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._lock = threading.RLock()
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workflow_execution_ownership (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                fencing_token INTEGER NOT NULL,
                ownership_json TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id, step_id)
            );
            CREATE TABLE IF NOT EXISTS workflow_execution_attempt_history (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                attempt_id TEXT NOT NULL,
                ownership_json TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id, step_id, revision)
            );
            CREATE TABLE IF NOT EXISTS workflow_retry_budgets (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                used INTEGER NOT NULL,
                maximum INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, run_id)
            );
            CREATE TABLE IF NOT EXISTS workflow_retry_consumptions (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                retry_id TEXT NOT NULL,
                category TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id, retry_id)
            );
            """
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def claim(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        owner_id: str,
        lease_seconds: float,
        maximum_retries: int,
        now: float | None = None,
    ) -> OwnershipClaim:
        timestamp = _validate_lease(lease_seconds, now)
        key = (str(tenant_id), str(run_id), str(step_id))
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read(*key)
                if current is not None and current.workflow_id != str(workflow_id):
                    raise OptimisticConcurrencyError("execution_ownership_workflow_binding_conflict")
                if current and current.status == "completed":
                    self._connection.commit()
                    return OwnershipClaim(current, False, "already_completed")
                if current and current.status == "active" and current.lease_expires_at > timestamp:
                    reason = "already_owned" if current.owner_id == str(owner_id) else "lease_held"
                    self._connection.commit()
                    return OwnershipClaim(current, False, reason)
                attempt_id = f"att-{uuid.uuid4().hex}"
                if current is not None:
                    self._consume_retry_in_transaction(
                        tenant_id=str(tenant_id),
                        run_id=str(run_id),
                        retry_id=attempt_id,
                        category="hub_task",
                        maximum=int(maximum_retries),
                    )
                ownership = ExecutionOwnership(
                    tenant_id=str(tenant_id),
                    workflow_id=str(workflow_id),
                    run_id=str(run_id),
                    step_id=str(step_id),
                    attempt_id=attempt_id,
                    owner_id=str(owner_id),
                    fencing_token=(current.fencing_token + 1) if current else 1,
                    revision=(current.revision + 1) if current else 1,
                    status="active",
                    lease_expires_at=timestamp + float(lease_seconds),
                    last_heartbeat_at=timestamp,
                )
                self._write(ownership, expected_revision=current.revision if current else 0)
                self._connection.commit()
                return OwnershipClaim(ownership, True, "acquired" if current is None else "recovered")
            except Exception:
                self._connection.rollback()
                raise

    def heartbeat(self, **values: Any) -> ExecutionOwnership:
        timestamp = _validate_lease(float(values["lease_seconds"]), values.get("now"))
        return self._mutate_owned(
            values,
            lambda current: _heartbeat(current, values=values, timestamp=timestamp),
        )

    def acknowledge_result(self, **values: Any) -> ExecutionOwnership:
        result_ack_key = str(values.get("result_ack_key") or "")
        if not result_ack_key:
            raise ValueError("result_ack_key_required")
        timestamp = _timestamp(values.get("now"))
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(values)
                _assert_owner_from_values(current, values)
                if current.status == "completed" and current.result_ack_key == result_ack_key:
                    self._connection.commit()
                    return current
                _assert_expected_revision(current, int(values["expected_revision"]))
                if current.status != "active" or current.lease_expires_at <= timestamp:
                    raise FencingTokenError("result_owner_not_active")
                updated = replace(
                    current,
                    revision=current.revision + 1,
                    status="completed",
                    result_ack_key=result_ack_key,
                    lease_expires_at=timestamp,
                )
                self._write(updated, expected_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def fail_attempt(self, *, failure_code: str, dead_letter: bool = False, **values: Any) -> ExecutionOwnership:
        def mutate(current: ExecutionOwnership) -> ExecutionOwnership:
            _assert_expected_revision(current, int(values["expected_revision"]))
            if current.status != "active":
                raise InvalidTransitionError("failure_requires_active_ownership")
            timestamp = _timestamp(values.get("now"))
            if current.lease_expires_at <= timestamp:
                raise FencingTokenError("failure_owner_lease_expired")
            return replace(
                current,
                revision=current.revision + 1,
                status="dead_letter" if dead_letter else "failed",
                failure_code=str(failure_code or "execution_failed"),
                lease_expires_at=timestamp,
            )

        return self._mutate_owned(values, mutate)

    def reconcile_orphan(
        self, *, tenant_id: str, run_id: str, step_id: str, now: float | None = None
    ) -> ExecutionOwnership | None:
        timestamp = float(now if now is not None else time.time())
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read(str(tenant_id), str(run_id), str(step_id))
                if current is None or current.status != "active" or current.lease_expires_at > timestamp:
                    self._connection.commit()
                    return None
                updated = replace(
                    current,
                    revision=current.revision + 1,
                    status="orphaned",
                    failure_code="lease_expired",
                )
                self._write(updated, expected_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def get(self, *, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        with self._lock:
            return self._read(str(tenant_id), str(run_id), str(step_id))

    def consume_retry(
        self,
        *,
        tenant_id: str,
        run_id: str,
        retry_id: str,
        category: str,
        maximum: int,
    ) -> RetryBudgetSnapshot:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                value = self._consume_retry_in_transaction(
                    tenant_id=str(tenant_id),
                    run_id=str(run_id),
                    retry_id=str(retry_id),
                    category=str(category),
                    maximum=int(maximum),
                )
                self._connection.commit()
                return value
            except Exception:
                self._connection.rollback()
                raise

    def get_retry_budget(self, *, tenant_id: str, run_id: str, maximum: int) -> RetryBudgetSnapshot:
        with self._lock:
            row = self._connection.execute(
                "SELECT used, maximum FROM workflow_retry_budgets WHERE tenant_id = ? AND run_id = ?",
                (str(tenant_id), str(run_id)),
            ).fetchone()
        if row is not None and int(row["maximum"]) != int(maximum):
            raise InvalidTransitionError("retry_budget_maximum_mismatch")
        return RetryBudgetSnapshot(
            str(tenant_id), str(run_id), used=int(row["used"] if row else 0), maximum=int(maximum)
        )

    def _consume_retry_in_transaction(
        self, *, tenant_id: str, run_id: str, retry_id: str, category: str, maximum: int
    ) -> RetryBudgetSnapshot:
        if maximum < 0 or not retry_id or category not in RETRY_CATEGORIES:
            raise ValueError("retry_budget_input_invalid")
        duplicate = self._connection.execute(
            """
            SELECT category FROM workflow_retry_consumptions
            WHERE tenant_id = ? AND run_id = ? AND retry_id = ?
            """,
            (tenant_id, run_id, retry_id),
        ).fetchone()
        row = self._connection.execute(
            "SELECT used, maximum FROM workflow_retry_budgets WHERE tenant_id = ? AND run_id = ?",
            (tenant_id, run_id),
        ).fetchone()
        used = int(row["used"] if row else 0)
        if row is not None and int(row["maximum"]) != maximum:
            raise InvalidTransitionError("retry_budget_maximum_mismatch")
        if duplicate and str(duplicate["category"]) != category:
            raise InvalidTransitionError("retry_budget_retry_id_binding_mismatch")
        if duplicate:
            return RetryBudgetSnapshot(tenant_id, run_id, used=used, maximum=maximum)
        if used >= maximum:
            raise InvalidTransitionError("retry_budget_exhausted")
        self._connection.execute(
            """
            INSERT INTO workflow_retry_consumptions (tenant_id, run_id, retry_id, category)
            VALUES (?, ?, ?, ?)
            """,
            (tenant_id, run_id, retry_id, category),
        )
        self._connection.execute(
            """
            INSERT INTO workflow_retry_budgets (tenant_id, run_id, used, maximum) VALUES (?, ?, 1, ?)
            ON CONFLICT (tenant_id, run_id) DO UPDATE SET used = used + 1
            """,
            (tenant_id, run_id, maximum),
        )
        return RetryBudgetSnapshot(tenant_id, run_id, used=used + 1, maximum=maximum)

    def _mutate_owned(
        self,
        values: dict[str, Any],
        mutate: Callable[[ExecutionOwnership], ExecutionOwnership],
    ) -> ExecutionOwnership:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_required(values)
                _assert_owner_from_values(current, values)
                updated = mutate(current)
                self._write(updated, expected_revision=current.revision)
                self._connection.commit()
                return updated
            except Exception:
                self._connection.rollback()
                raise

    def _read(self, tenant_id: str, run_id: str, step_id: str) -> ExecutionOwnership | None:
        row = self._connection.execute(
            """
            SELECT ownership_json FROM workflow_execution_ownership
            WHERE tenant_id = ? AND run_id = ? AND step_id = ?
            """,
            (tenant_id, run_id, step_id),
        ).fetchone()
        return ExecutionOwnership.from_mapping(json.loads(str(row["ownership_json"]))) if row else None

    def _read_required(self, values: dict[str, Any]) -> ExecutionOwnership:
        current = self._read(str(values["tenant_id"]), str(values["run_id"]), str(values["step_id"]))
        if current is None:
            raise KeyError("execution_ownership_not_found")
        return current

    def _write(self, value: ExecutionOwnership, *, expected_revision: int) -> None:
        value.assert_valid()
        payload = canonical_json(value.to_dict())
        if expected_revision == 0:
            self._connection.execute(
                """
                INSERT INTO workflow_execution_ownership
                (tenant_id, run_id, step_id, revision, fencing_token, ownership_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (value.tenant_id, value.run_id, value.step_id, value.revision, value.fencing_token, payload),
            )
        else:
            cursor = self._connection.execute(
                """
                UPDATE workflow_execution_ownership
                SET revision = ?, fencing_token = ?, ownership_json = ?
                WHERE tenant_id = ? AND run_id = ? AND step_id = ? AND revision = ?
                """,
                (
                    value.revision,
                    value.fencing_token,
                    payload,
                    value.tenant_id,
                    value.run_id,
                    value.step_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise OptimisticConcurrencyError("execution_ownership_compare_and_set_failed")
        self._connection.execute(
            """
            INSERT INTO workflow_execution_attempt_history
            (tenant_id, run_id, step_id, revision, attempt_id, ownership_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (value.tenant_id, value.run_id, value.step_id, value.revision, value.attempt_id, payload),
        )


def _validate_lease(lease_seconds: float, now: Any) -> float:
    if float(lease_seconds) <= 0:
        raise ValueError("lease_seconds_invalid")
    return float(now if now is not None else time.time())


def _timestamp(value: Any) -> float:
    return float(time.time() if value is None else value)


def _assert_owner(
    current: ExecutionOwnership, *, attempt_id: str, owner_id: str, fencing_token: int
) -> None:
    if (
        current.attempt_id != attempt_id
        or current.owner_id != owner_id
        or current.fencing_token != int(fencing_token)
    ):
        raise FencingTokenError("execution_owner_stale")


def _assert_owner_from_values(current: ExecutionOwnership, values: dict[str, Any]) -> None:
    _assert_owner(
        current,
        attempt_id=str(values["attempt_id"]),
        owner_id=str(values["owner_id"]),
        fencing_token=int(values["fencing_token"]),
    )


def _assert_expected_revision(current: ExecutionOwnership, expected_revision: int) -> None:
    if current.revision != int(expected_revision):
        raise OptimisticConcurrencyError(
            f"execution_ownership_revision_conflict:expected={expected_revision}:actual={current.revision}"
        )


def _heartbeat(
    current: ExecutionOwnership, *, values: dict[str, Any], timestamp: float
) -> ExecutionOwnership:
    _assert_expected_revision(current, int(values["expected_revision"]))
    if current.status != "active":
        raise FencingTokenError("heartbeat_owner_no_longer_active")
    if current.lease_expires_at <= timestamp:
        raise FencingTokenError("ownership_lease_expired")
    return replace(
        current,
        revision=current.revision + 1,
        last_heartbeat_at=timestamp,
        lease_expires_at=timestamp + float(values["lease_seconds"]),
    )
