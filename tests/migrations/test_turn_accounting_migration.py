import runpy
from pathlib import Path

from agent.db_models.turn_accounting import (
    TurnAccountingLedgerDB,
    TurnAccountingSourceCursorDB,
)


def test_turn_accounting_migration_extends_current_single_head():
    migration = runpy.run_path(
        Path(__file__).resolve().parents[2]
        / "migrations/versions/c46bf2a3d5e7_add_turn_accounting_ledger.py"
    )

    assert migration["revision"] == "c46bf2a3d5e7"
    assert migration["down_revision"] == "b35ae1f2c4d6"


def test_turn_accounting_schema_contains_only_pseudonymous_scope_columns():
    columns = {
        *TurnAccountingLedgerDB.__table__.columns.keys(),
        *TurnAccountingSourceCursorDB.__table__.columns.keys(),
    }
    assert {"tenant_pseudonym", "pool_pseudonym", "allocation_pseudonym"} <= columns
    assert not columns & {
        "ip",
        "secret",
        "username",
        "token",
        "participant_id",
        "credential_id",
    }
