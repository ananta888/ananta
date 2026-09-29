"""SQLite schema preflight for the direct execution ownership tables and receipt row decoding."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Mapping

from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.ownership_records import ExecutionOwnership
from agent.services.workflow_runtime.ownership_transition_errors import WorkflowTransitionOwnershipReservationConflict
from agent.services.workflow_runtime.ownership_transition_reservation import (
    WorkflowTransitionOwnershipReservationReceipt,
)


def _direct_exact_current(row: sqlite3.Row) -> ExecutionOwnership:
    try:
        raw = json.loads(str(row["ownership_json"]))
        if not isinstance(raw, Mapping):
            raise TypeError("ownership")
        value = ExecutionOwnership.from_exact_mapping(raw)
        if (
            value.tenant_id != row["tenant_id"]
            or value.run_id != row["run_id"]
            or value.step_id != row["step_id"]
            or value.revision != row["revision"]
            or value.fencing_token != row["fencing_token"]
        ):
            raise ValueError("projection")
        return value
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_current_projection_conflict"
        ) from exc


def _direct_exact_history(row: sqlite3.Row) -> ExecutionOwnership:
    try:
        raw = json.loads(str(row["ownership_json"]))
        if not isinstance(raw, Mapping):
            raise TypeError("ownership")
        value = ExecutionOwnership.from_exact_mapping(raw)
        if (
            value.tenant_id != row["tenant_id"]
            or value.run_id != row["run_id"]
            or value.step_id != row["step_id"]
            or value.revision != row["revision"]
            or value.attempt_id != row["attempt_id"]
        ):
            raise ValueError("projection")
        return value
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_history_projection_conflict"
        ) from exc


def _direct_transition_reservation_receipt_values(
    receipt: WorkflowTransitionOwnershipReservationReceipt,
) -> tuple[object, ...]:
    intent = receipt.intent
    return (
        receipt.receipt_id,
        receipt.transition_id,
        receipt.effect_id,
        receipt.operation_fence_id,
        receipt.attempt_id,
        receipt.owner_id,
        intent.tenant_id,
        intent.workflow_id,
        intent.run_id,
        intent.runtime_id,
        intent.step_id,
        intent.ownership_intent_digest,
        receipt.acquired_record_digest,
        receipt.receipt_digest,
        receipt.creator_claim_generation,
        receipt.acquired_revision,
        receipt.acquired_fencing_token,
        intent.maximum_retries,
        int(receipt.retry_consumed),
        intent.planned_at,
        receipt.reserved_at,
        receipt.lease_expires_at,
        canonical_json(receipt.to_dict()),
    )


def _direct_transition_reservation_receipt(
    row: sqlite3.Row,
) -> WorkflowTransitionOwnershipReservationReceipt:
    try:
        for name in (
            "receipt_id",
            "transition_id",
            "effect_id",
            "operation_fence_id",
            "attempt_id",
            "owner_id",
            "tenant_id",
            "workflow_id",
            "run_id",
            "runtime_id",
            "step_id",
            "ownership_intent_digest",
            "acquisition_record_digest",
            "receipt_digest",
            "receipt_json",
        ):
            if type(row[name]) is not str:
                raise TypeError(name)
        for name in (
            "creator_claim_generation",
            "acquired_revision",
            "acquired_fencing_token",
            "maximum_retries",
            "retry_consumed",
        ):
            if type(row[name]) is not int:
                raise TypeError(name)
        for name in ("planned_at", "reserved_at", "lease_expires_at"):
            if type(row[name]) is not float or not math.isfinite(row[name]):
                raise TypeError(name)
        raw = json.loads(str(row["receipt_json"]))
        if not isinstance(raw, Mapping):
            raise TypeError("receipt")
        receipt = WorkflowTransitionOwnershipReservationReceipt.from_mapping(raw)
        expected = _direct_transition_reservation_receipt_values(receipt)[:-1]
        actual = tuple(
            row[name]
            for name in (
                "receipt_id",
                "transition_id",
                "effect_id",
                "operation_fence_id",
                "attempt_id",
                "owner_id",
                "tenant_id",
                "workflow_id",
                "run_id",
                "runtime_id",
                "step_id",
                "ownership_intent_digest",
                "acquisition_record_digest",
                "receipt_digest",
                "creator_claim_generation",
                "acquired_revision",
                "acquired_fencing_token",
                "maximum_retries",
                "retry_consumed",
                "planned_at",
                "reserved_at",
                "lease_expires_at",
            )
        )
        if actual != expected:
            raise ValueError("projection")
        return receipt
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkflowTransitionOwnershipReservationConflict(
            "workflow_transition_ownership_receipt_projection_conflict"
        ) from exc


_DIRECT_OWNERSHIP_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "workflow_execution_ownership": (
        "tenant_id",
        "run_id",
        "step_id",
        "revision",
        "fencing_token",
        "ownership_json",
    ),
    "workflow_execution_attempt_history": (
        "tenant_id",
        "run_id",
        "step_id",
        "revision",
        "attempt_id",
        "ownership_json",
    ),
    "workflow_retry_budgets": ("tenant_id", "run_id", "used", "maximum"),
    "workflow_retry_consumptions": ("tenant_id", "run_id", "retry_id", "category"),
    "workflow_transition_ownership_reservations": (
        "receipt_id",
        "transition_id",
        "effect_id",
        "operation_fence_id",
        "attempt_id",
        "owner_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "runtime_id",
        "step_id",
        "ownership_intent_digest",
        "acquisition_record_digest",
        "receipt_digest",
        "creator_claim_generation",
        "acquired_revision",
        "acquired_fencing_token",
        "maximum_retries",
        "retry_consumed",
        "planned_at",
        "reserved_at",
        "lease_expires_at",
        "receipt_json",
    ),
}
_DIRECT_OWNERSHIP_TABLE_TYPES: dict[str, tuple[str, ...]] = {
    "workflow_execution_ownership": ("TEXT", "TEXT", "TEXT", "INTEGER", "INTEGER", "TEXT"),
    "workflow_execution_attempt_history": (
        "TEXT",
        "TEXT",
        "TEXT",
        "INTEGER",
        "TEXT",
        "TEXT",
    ),
    "workflow_retry_budgets": ("TEXT", "TEXT", "INTEGER", "INTEGER"),
    "workflow_retry_consumptions": ("TEXT", "TEXT", "TEXT", "TEXT"),
    "workflow_transition_ownership_reservations": (
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "TEXT",
        "INTEGER",
        "INTEGER",
        "INTEGER",
        "INTEGER",
        "INTEGER",
        "REAL",
        "REAL",
        "REAL",
        "TEXT",
    ),
}
_DIRECT_OWNERSHIP_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "workflow_execution_ownership": ("tenant_id", "run_id", "step_id"),
    "workflow_execution_attempt_history": (
        "tenant_id",
        "run_id",
        "step_id",
        "revision",
    ),
    "workflow_retry_budgets": ("tenant_id", "run_id"),
    "workflow_retry_consumptions": ("tenant_id", "run_id", "retry_id"),
    "workflow_transition_ownership_reservations": ("receipt_id",),
}
_DIRECT_RESERVATION_INDEXES = {
    "ix_workflow_transition_ownership_res_transition": ("transition_id",),
    "ix_workflow_transition_ownership_res_tenant_run": ("tenant_id", "run_id"),
    "ix_workflow_transition_ownership_res_scope": ("tenant_id", "run_id", "step_id"),
    "ix_workflow_transition_ownership_res_owner": ("owner_id",),
}
_DIRECT_RESERVATION_UNIQUES = (
    (("receipt_id",), "pk"),
    (("effect_id",), "u"),
    (("operation_fence_id",), "u"),
    (("attempt_id",), "u"),
    (("tenant_id", "run_id", "step_id", "acquired_revision"), "u"),
    (("tenant_id", "run_id", "step_id", "acquired_fencing_token"), "u"),
)
_DIRECT_RESERVATION_CHECK_TERMS = (
    "creator_claim_generation > 0",
    "acquired_revision > 0",
    "acquired_revision <= 2147483647",
    "acquired_fencing_token > 0",
    "acquired_fencing_token <= 2147483647",
    "maximum_retries >= 0",
    "maximum_retries <= 2147483647",
    "retry_consumed IN (0, 1)",
    "planned_at > 0",
    "reserved_at >= planned_at",
    "lease_expires_at > reserved_at",
)


def _preflight_direct_ownership_schema(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    for table, columns in _DIRECT_OWNERSHIP_TABLE_COLUMNS.items():
        if table in tables:
            _assert_direct_ownership_table(connection, table, columns)
    index_rows = connection.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'").fetchall()
    indexes = {str(row[0]): str(row[1]) for row in index_rows}
    for name in _DIRECT_RESERVATION_INDEXES:
        if name in indexes and indexes[name] != "workflow_transition_ownership_reservations":
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    if "workflow_transition_ownership_reservations" in tables:
        _assert_direct_transition_reservation_schema(connection)


def _assert_direct_transition_reservation_schema(connection: sqlite3.Connection) -> None:
    table = "workflow_transition_ownership_reservations"
    _assert_direct_ownership_table(
        connection,
        table,
        _DIRECT_OWNERSHIP_TABLE_COLUMNS[table],
    )
    unique_columns: list[tuple[tuple[str, ...], str]] = []
    actual_indexes: dict[str, tuple[str, ...]] = {}
    for index in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
        name = str(index[1])
        columns = tuple(str(value[2]) for value in connection.execute(f'PRAGMA index_info("{name}")').fetchall())
        if int(index[4]) != 0:
            raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
        if int(index[2]) == 1:
            unique_columns.append((columns, str(index[3])))
        else:
            actual_indexes[name] = columns
    if sorted(unique_columns) != sorted(_DIRECT_RESERVATION_UNIQUES) or actual_indexes != _DIRECT_RESERVATION_INDEXES:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    if connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    check_expression = _direct_named_check_expression(
        str(sql_row[0] if sql_row else ""),
        "ck_workflow_transition_ownership_res_valid",
    )
    actual_terms = sorted(_direct_check_term(value) for value in re.split(r"\band\b", check_expression, flags=re.I))
    expected_terms = sorted(_direct_check_term(value) for value in _DIRECT_RESERVATION_CHECK_TERMS)
    if actual_terms != expected_terms:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")


def _direct_named_check_expression(sql: str, name: str) -> str:
    if len(tuple(re.finditer(r"\bcheck\s*\(", sql, flags=re.I))) != 1:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    matches = tuple(
        re.finditer(
            rf"\bconstraint\s+{re.escape(name)}\s+check\s*\(",
            sql,
            flags=re.I,
        )
    )
    if len(matches) != 1:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    start = matches[0].end()
    depth = 1
    for position in range(start, len(sql)):
        character = sql[position]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return sql[start:position]
    raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")


def _direct_check_term(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).lower()
    while normalized.startswith("(") and normalized.endswith(")"):
        depth = 0
        encloses_all = True
        for index, character in enumerate(normalized):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and index != len(normalized) - 1:
                    encloses_all = False
                    break
            if depth < 0:
                encloses_all = False
                break
        if not encloses_all or depth != 0:
            break
        normalized = normalized[1:-1]
    if not normalized:
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
    return normalized


def _assert_direct_ownership_table(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> None:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    actual_columns = tuple(str(row[1]) for row in rows)
    actual_types = tuple(str(row[2]).upper() for row in rows)
    primary_key = tuple(name for _, name in sorted((int(row[5]), str(row[1])) for row in rows if int(row[5]) > 0))
    unexpected_indexes = False
    if table != "workflow_transition_ownership_reservations":
        unexpected_indexes = any(
            str(row[3]) != "pk" for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall()
        )
    if (
        actual_columns != columns
        or actual_types != _DIRECT_OWNERSHIP_TABLE_TYPES[table]
        or primary_key != _DIRECT_OWNERSHIP_PRIMARY_KEYS[table]
        or any(int(row[3]) != 1 for row in rows)
        or unexpected_indexes
        or connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    ):
        raise WorkflowTransitionOwnershipReservationConflict("workflow_transition_ownership_direct_schema_conflict")
