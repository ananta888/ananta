"""Add append-only workflow transition ownership reservation receipts.

Revision ID: f0b2d4e6a8c1
Revises: e9a2c4d6f8b0
Create Date: 2026-08-15
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "f0b2d4e6a8c1"
down_revision: str | Sequence[str] | None = "e9a2c4d6f8b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_transition_ownership_reservations"
_TRANSITION_INDEX = "ix_workflow_transition_ownership_res_transition"
_TENANT_RUN_INDEX = "ix_workflow_transition_ownership_res_tenant_run"
_SCOPE_INDEX = "ix_workflow_transition_ownership_res_scope"
_OWNER_INDEX = "ix_workflow_transition_ownership_res_owner"

_STRING_LENGTHS = {
    "receipt_id": 256,
    "transition_id": 256,
    "effect_id": 256,
    "operation_fence_id": 256,
    "attempt_id": 256,
    "owner_id": 256,
    "tenant_id": 256,
    "workflow_id": 256,
    "run_id": 256,
    "runtime_id": 64,
    "step_id": 256,
    "ownership_intent_digest": 64,
    "acquisition_record_digest": 64,
    "receipt_digest": 64,
}
_BIG_INTEGERS = {
    "creator_claim_generation",
    "acquired_revision",
    "acquired_fencing_token",
}
_INTEGERS = {"maximum_retries"}
_BOOLEANS = {"retry_consumed"}
_FLOATS = {"planned_at", "reserved_at", "lease_expires_at"}
_TARGET_TYPES: dict[str, sa.types.TypeEngine] = {
    **{name: sa.String(length) for name, length in _STRING_LENGTHS.items()},
    **{name: sa.BigInteger() for name in _BIG_INTEGERS},
    **{name: sa.Integer() for name in _INTEGERS},
    **{name: sa.Boolean() for name in _BOOLEANS},
    **{name: sa.Float() for name in _FLOATS},
    "receipt": sa.JSON(),
}
_UNIQUE_COLUMNS = {
    "uq_workflow_transition_ownership_res_effect": ("effect_id",),
    "uq_workflow_transition_ownership_res_fence": ("operation_fence_id",),
    "uq_workflow_transition_ownership_res_attempt": ("attempt_id",),
    "uq_workflow_transition_ownership_res_revision": (
        "tenant_id",
        "run_id",
        "step_id",
        "acquired_revision",
    ),
    "uq_workflow_transition_ownership_res_current_fence": (
        "tenant_id",
        "run_id",
        "step_id",
        "acquired_fencing_token",
    ),
}
_INDEX_COLUMNS = {
    _TRANSITION_INDEX: ("transition_id",),
    _TENANT_RUN_INDEX: ("tenant_id", "run_id"),
    _SCOPE_INDEX: ("tenant_id", "run_id", "step_id"),
    _OWNER_INDEX: ("owner_id",),
}
_CHECK_NAME = "ck_workflow_transition_ownership_res_valid"
_CHECK_SQL = (
    "creator_claim_generation > 0 "
    "AND acquired_revision > 0 "
    "AND acquired_revision <= 2147483647 "
    "AND acquired_fencing_token > 0 "
    "AND acquired_fencing_token <= 2147483647 "
    "AND maximum_retries >= 0 "
    "AND maximum_retries <= 2147483647 "
    "AND (retry_consumed = FALSE OR retry_consumed = TRUE) "
    "AND planned_at > 0 "
    "AND reserved_at >= planned_at "
    "AND lease_expires_at > reserved_at"
)
_POSTGRESQL_CAST = re.compile(
    r"::\s*(?:bigint|integer|smallint|numeric|real|double\s+precision)\b",
    flags=re.IGNORECASE,
)
_SQL_ATOM = r"(?:[a-z_][a-z0-9_]*|[+-]?\d+(?:\.\d+)?|true|false)"
_PARENTHESIZED_ATOM = re.compile(rf"\(({_SQL_ATOM})\)")
_PARENTHESIZED_COMPARISON = re.compile(rf"\(({_SQL_ATOM})(?:<=|>=|<>|!=|=|<|>)({_SQL_ATOM})\)")
_REDUNDANT_NESTED_PARENTHESES = re.compile(r"\(\(([^()]*)\)\)")

_PREREQUISITES: dict[str, dict[str, object]] = {
    "workflow_execution_ownership": {
        "columns": {
            "id": sa.String(),
            "tenant_id": sa.String(),
            "workflow_id": sa.String(),
            "run_id": sa.String(),
            "step_id": sa.String(),
            "attempt_id": sa.String(),
            "owner_id": sa.String(),
            "status": sa.String(),
            "revision": sa.Integer(),
            "fencing_token": sa.Integer(),
            "lease_expires_at": sa.Float(),
            "last_heartbeat_at": sa.Float(),
            "ownership": sa.JSON(),
        },
        "primary_key": ("id",),
        "unique": {
            "uq_workflow_execution_ownership_step": (
                "tenant_id",
                "run_id",
                "step_id",
            ),
        },
    },
    "workflow_execution_attempt_history": {
        "columns": {
            "id": sa.String(),
            "tenant_id": sa.String(),
            "workflow_id": sa.String(),
            "run_id": sa.String(),
            "step_id": sa.String(),
            "attempt_id": sa.String(),
            "owner_id": sa.String(),
            "status": sa.String(),
            "revision": sa.Integer(),
            "fencing_token": sa.Integer(),
            "recorded_at": sa.Float(),
            "ownership": sa.JSON(),
        },
        "primary_key": ("id",),
        "unique": {
            "uq_workflow_execution_attempt_revision": (
                "tenant_id",
                "run_id",
                "step_id",
                "revision",
            ),
        },
    },
    "workflow_retry_budgets": {
        "columns": {
            "id": sa.String(),
            "tenant_id": sa.String(),
            "run_id": sa.String(),
            "used": sa.Integer(),
            "maximum": sa.Integer(),
            "revision": sa.Integer(),
            "updated_at": sa.Float(),
        },
        "primary_key": ("id",),
        "unique": {
            "uq_workflow_retry_budget_run": ("tenant_id", "run_id"),
        },
    },
    "workflow_retry_consumptions": {
        "columns": {
            "id": sa.String(),
            "tenant_id": sa.String(),
            "run_id": sa.String(),
            "retry_id": sa.String(),
            "category": sa.String(),
            "consumed_at": sa.Float(),
        },
        "primary_key": ("id",),
        "unique": {
            "uq_workflow_retry_consumption_id": (
                "tenant_id",
                "run_id",
                "retry_id",
            ),
        },
    },
}


def _create_table() -> None:
    op.create_table(
        _TABLE,
        sa.Column("receipt_id", sa.String(256), primary_key=True),
        sa.Column("transition_id", sa.String(256), nullable=False),
        sa.Column("effect_id", sa.String(256), nullable=False),
        sa.Column("operation_fence_id", sa.String(256), nullable=False),
        sa.Column("attempt_id", sa.String(256), nullable=False),
        sa.Column("owner_id", sa.String(256), nullable=False),
        sa.Column("tenant_id", sa.String(256), nullable=False),
        sa.Column("workflow_id", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("runtime_id", sa.String(64), nullable=False),
        sa.Column("step_id", sa.String(256), nullable=False),
        sa.Column("ownership_intent_digest", sa.String(64), nullable=False),
        sa.Column("acquisition_record_digest", sa.String(64), nullable=False),
        sa.Column("receipt_digest", sa.String(64), nullable=False),
        sa.Column("creator_claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("acquired_revision", sa.BigInteger(), nullable=False),
        sa.Column("acquired_fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("maximum_retries", sa.Integer(), nullable=False),
        sa.Column("retry_consumed", sa.Boolean(), nullable=False),
        sa.Column("planned_at", sa.Float(), nullable=False),
        sa.Column("reserved_at", sa.Float(), nullable=False),
        sa.Column("lease_expires_at", sa.Float(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "effect_id",
            name="uq_workflow_transition_ownership_res_effect",
        ),
        sa.UniqueConstraint(
            "operation_fence_id",
            name="uq_workflow_transition_ownership_res_fence",
        ),
        sa.UniqueConstraint(
            "attempt_id",
            name="uq_workflow_transition_ownership_res_attempt",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            "acquired_revision",
            name="uq_workflow_transition_ownership_res_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            "acquired_fencing_token",
            name="uq_workflow_transition_ownership_res_current_fence",
        ),
        sa.CheckConstraint(
            _CHECK_SQL,
            name=_CHECK_NAME,
        ),
    )


def _type_shape_matches(actual: sa.types.TypeEngine, expected: sa.types.TypeEngine) -> bool:
    if isinstance(expected, sa.String):
        return (
            isinstance(actual, sa.String)
            and not isinstance(actual, (sa.Text, sa.CHAR))
            and actual.length == expected.length
        )
    if isinstance(expected, sa.BigInteger):
        return isinstance(actual, sa.BigInteger)
    if isinstance(expected, sa.Integer):
        return isinstance(actual, sa.Integer) and not isinstance(actual, (sa.BigInteger, sa.SmallInteger))
    if isinstance(expected, sa.Boolean):
        return isinstance(actual, sa.Boolean)
    if isinstance(expected, sa.Float):
        return isinstance(actual, sa.Float) and not isinstance(actual, sa.REAL)
    if isinstance(expected, sa.JSON):
        return isinstance(actual, sa.JSON) and actual.__class__.__name__.lower() != "jsonb"
    return type(actual) is type(expected)


def _validate_prerequisites(inspector: sa.Inspector) -> None:
    for table_name, specification in _PREREQUISITES.items():
        reflected = {value["name"]: value for value in inspector.get_columns(table_name)}
        required = specification["columns"]
        if not isinstance(required, dict) or not set(required).issubset(reflected):
            raise RuntimeError("workflow_transition_ownership_reservation_prerequisite_conflict")
        for name, expected_type in required.items():
            column = reflected[name]
            if (
                column["nullable"]
                or not isinstance(expected_type, sa.types.TypeEngine)
                or not _type_shape_matches(column["type"], expected_type)
            ):
                raise RuntimeError("workflow_transition_ownership_reservation_prerequisite_conflict")
        primary_key = inspector.get_pk_constraint(table_name)
        if tuple(primary_key.get("constrained_columns") or ()) != specification["primary_key"]:
            raise RuntimeError("workflow_transition_ownership_reservation_prerequisite_conflict")
        actual_unique = {
            value["name"]: tuple(value["column_names"]) for value in inspector.get_unique_constraints(table_name)
        }
        required_unique = specification["unique"]
        if not isinstance(required_unique, dict) or any(
            actual_unique.get(name) != columns for name, columns in required_unique.items()
        ):
            raise RuntimeError("workflow_transition_ownership_reservation_prerequisite_conflict")


def _create_index_if_missing(name: str, columns: list[str]) -> None:
    existing = {value["name"] for value in inspect(op.get_bind()).get_indexes(_TABLE)}
    if name not in existing:
        op.create_index(name, _TABLE, columns, unique=False)


def _validate_index_name_ownership(inspector: sa.Inspector) -> None:
    """Fail deterministically before DDL when an index name is already foreign."""

    for table_name in inspector.get_table_names():
        if table_name == _TABLE:
            continue
        if any(value.get("name") in _INDEX_COLUMNS for value in inspector.get_indexes(table_name)):
            raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")


def _has_nondefault_dialect_options(
    value: Mapping[str, object],
    *,
    allow_empty_postgresql_include: bool = False,
    allow_postgresql_nulls_distinct: bool = False,
) -> bool:
    raw = value.get("dialect_options")
    if raw is None:
        return False
    if not isinstance(raw, Mapping):
        return True
    options = dict(raw)
    if allow_empty_postgresql_include and options.get("postgresql_include") in ([], ()):
        options.pop("postgresql_include")
    if allow_postgresql_nulls_distinct and options.get("postgresql_nulls_not_distinct") is False:
        options.pop("postgresql_nulls_not_distinct")
    return bool(options)


def _target_index_shape_matches(value: Mapping[str, object], name: str) -> bool:
    return (
        value.get("name") == name
        and tuple(value.get("column_names") or ()) == _INDEX_COLUMNS[name]
        and not value.get("unique")
        and not value.get("expressions")
        and not value.get("column_sorting")
        and not tuple(value.get("include_columns") or ())
        and not _has_nondefault_dialect_options(
            value,
            allow_empty_postgresql_include=True,
        )
    )


def _constraint_index_shape_matches(
    value: Mapping[str, object],
    *,
    constraint_name: str,
    columns: tuple[str, ...],
) -> bool:
    return (
        value.get("duplicates_constraint") == constraint_name
        and value.get("name") == constraint_name
        and value.get("unique") is True
        and tuple(value.get("column_names") or ()) == columns
        and not value.get("expressions")
        and not value.get("column_sorting")
        and not tuple(value.get("include_columns") or ())
        and not _has_nondefault_dialect_options(
            value,
            allow_empty_postgresql_include=True,
            allow_postgresql_nulls_distinct=True,
        )
    )


def _validated_target_indexes(inspector: sa.Inspector) -> dict[str, tuple[str, ...]]:
    constraint_columns = {
        value["name"]: tuple(value.get("column_names") or ())
        for value in inspector.get_unique_constraints(_TABLE)
        if isinstance(value.get("name"), str)
    }
    primary_key = inspector.get_pk_constraint(_TABLE)
    primary_key_name = primary_key.get("name")
    if isinstance(primary_key_name, str):
        constraint_columns[primary_key_name] = tuple(primary_key.get("constrained_columns") or ())
    indexes: dict[str, tuple[str, ...]] = {}
    for value in inspector.get_indexes(_TABLE):
        duplicate = value.get("duplicates_constraint")
        if duplicate is not None:
            if (
                not isinstance(duplicate, str)
                or duplicate not in constraint_columns
                or not _constraint_index_shape_matches(
                    value,
                    constraint_name=duplicate,
                    columns=constraint_columns[duplicate],
                )
            ):
                raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")
            continue
        name = value.get("name")
        columns = tuple(value.get("column_names") or ())
        if not isinstance(name, str) or name not in _INDEX_COLUMNS or not _target_index_shape_matches(value, name):
            raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")
        indexes[name] = columns
    return indexes


def _strip_whole_expression_parentheses(value: str) -> str:
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        encloses_whole_expression = True
        for position, character in enumerate(value):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and position != len(value) - 1:
                    encloses_whole_expression = False
                    break
            if depth < 0:
                encloses_whole_expression = False
                break
        if not encloses_whole_expression or depth != 0:
            break
        value = value[1:-1]
    return value


def _normalized_check_expression(sqltext: object) -> str:
    """Preserve the Boolean tree while removing reflection-only syntax."""

    if not isinstance(sqltext, str) or not sqltext.strip():
        return ""
    normalized = re.sub(r"^\s*check\s*", "", sqltext, flags=re.IGNORECASE).lower()
    normalized = _POSTGRESQL_CAST.sub("", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.translate(str.maketrans("", "", '"[]'))
    while True:
        previous = normalized
        normalized = _PARENTHESIZED_ATOM.sub(r"\1", normalized)
        normalized = _PARENTHESIZED_COMPARISON.sub(lambda match: match.group(0)[1:-1], normalized)
        normalized = _REDUNDANT_NESTED_PARENTHESES.sub(r"(\1)", normalized)
        normalized = _strip_whole_expression_parentheses(normalized)
        if normalized == previous:
            return normalized


def _validate_table(*, require_indexes: bool) -> None:
    inspector = inspect(op.get_bind())
    columns = {value["name"]: value for value in inspector.get_columns(_TABLE)}
    if (
        set(columns) != set(_TARGET_TYPES)
        or any(value["nullable"] for value in columns.values())
        or any(not _type_shape_matches(columns[name]["type"], expected) for name, expected in _TARGET_TYPES.items())
    ):
        raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")
    primary_key = inspector.get_pk_constraint(_TABLE)
    if tuple(primary_key.get("constrained_columns") or ()) != ("receipt_id",):
        raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")
    unique_constraints: dict[str, tuple[str, ...]] = {}
    for value in inspector.get_unique_constraints(_TABLE):
        name = value.get("name")
        if (
            not isinstance(name, str)
            or _has_nondefault_dialect_options(
                value,
                allow_empty_postgresql_include=True,
                allow_postgresql_nulls_distinct=True,
            )
            or value.get("duplicates_index")
        ):
            raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")
        unique_constraints[name] = tuple(value.get("column_names") or ())
    if unique_constraints != _UNIQUE_COLUMNS:
        raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")
    checks = inspector.get_check_constraints(_TABLE)
    if (
        len(checks) != 1
        or checks[0].get("name") != _CHECK_NAME
        or _has_nondefault_dialect_options(checks[0])
        or _normalized_check_expression(checks[0].get("sqltext")) != _normalized_check_expression(_CHECK_SQL)
    ):
        raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")
    if inspector.get_foreign_keys(_TABLE):
        raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")
    indexes = _validated_target_indexes(inspector)
    if require_indexes and indexes != _INDEX_COLUMNS:
        raise RuntimeError("workflow_transition_ownership_reservation_schema_conflict")


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    missing = set(_PREREQUISITES) - tables
    if missing:
        raise RuntimeError("workflow_transition_ownership_reservation_prerequisite_missing")
    _validate_prerequisites(inspector)
    _validate_index_name_ownership(inspector)
    if _TABLE not in tables:
        _create_table()
    _validate_table(require_indexes=False)
    for name, columns in _INDEX_COLUMNS.items():
        _create_index_if_missing(name, list(columns))
    _validate_table(require_indexes=True)


def downgrade() -> None:
    if _TABLE not in set(inspect(op.get_bind()).get_table_names()):
        return
    _validate_table(require_indexes=True)
    op.drop_table(_TABLE)
