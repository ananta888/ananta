"""Persist bounded semantic-compute negotiation progress.

Revision ID: ff4a5b6c7d8e
Revises: fe3f4a5b6c7d
Create Date: 2026-07-20 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "ff4a5b6c7d8e"
down_revision: str | Sequence[str] | None = "fe3f4a5b6c7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONTRACT_COLUMNS = (
    ("negotiation_started_at_ms", sa.BigInteger(), "0"),
    ("negotiation_round_count", sa.Integer(), "1"),
    ("negotiation_message_count", sa.Integer(), "1"),
)
_RECEIPT_COLUMNS = (
    ("result_negotiation_started_at_ms", sa.BigInteger(), "0"),
    ("result_negotiation_round_count", sa.Integer(), "1"),
    ("result_negotiation_message_count", sa.Integer(), "1"),
)


def _column_names(table: str) -> set[str]:
    return {str(column["name"]) for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "semantic_compute_contracts" in tables:
        existing = _column_names("semantic_compute_contracts")
        for name, column_type, default in _CONTRACT_COLUMNS:
            if name not in existing:
                op.add_column(
                    "semantic_compute_contracts",
                    sa.Column(
                        name,
                        column_type,
                        nullable=False,
                        server_default=sa.text(default),
                    ),
                )
        # A legacy revision is a conservative upper bound for both message and
        # round usage.  It may fail closed early, but can never reset a budget.
        op.execute(
            sa.text(
                "UPDATE semantic_compute_contracts "
                "SET negotiation_started_at_ms = CAST(created_at * 1000 AS BIGINT), "
                "negotiation_round_count = CASE WHEN revision > 0 THEN revision ELSE 1 END, "
                "negotiation_message_count = CASE WHEN revision > 0 THEN revision ELSE 1 END "
                "WHERE negotiation_started_at_ms = 0"
            )
        )

    if "semantic_contract_mutations" in tables:
        existing = _column_names("semantic_contract_mutations")
        for name, column_type, default in _RECEIPT_COLUMNS:
            if name not in existing:
                op.add_column(
                    "semantic_contract_mutations",
                    sa.Column(
                        name,
                        column_type,
                        nullable=False,
                        server_default=sa.text(default),
                    ),
                )
        op.execute(
            sa.text(
                "UPDATE semantic_contract_mutations "
                "SET result_negotiation_started_at_ms = CAST(created_at * 1000 AS BIGINT), "
                "result_negotiation_round_count = "
                "CASE WHEN result_revision > 0 THEN result_revision ELSE 1 END, "
                "result_negotiation_message_count = "
                "CASE WHEN result_revision > 0 THEN result_revision ELSE 1 END "
                "WHERE result_negotiation_started_at_ms = 0"
            )
        )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "semantic_contract_mutations" in tables:
        existing = _column_names("semantic_contract_mutations")
        with op.batch_alter_table("semantic_contract_mutations") as batch:
            for name, _column_type, _default in reversed(_RECEIPT_COLUMNS):
                if name in existing:
                    batch.drop_column(name)
    if "semantic_compute_contracts" in tables:
        existing = _column_names("semantic_compute_contracts")
        with op.batch_alter_table("semantic_compute_contracts") as batch:
            for name, _column_type, _default in reversed(_CONTRACT_COLUMNS):
                if name in existing:
                    batch.drop_column(name)
