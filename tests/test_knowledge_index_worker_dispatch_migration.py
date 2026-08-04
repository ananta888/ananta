from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from agent.db_models import KnowledgeIndexWorkerDispatchReceiptDB


def test_worker_dispatch_result_outbox_schema_matches_model_and_is_restart_safe(
    monkeypatch,
) -> None:
    receipt_migration = importlib.import_module(
        "migrations.versions."
        "f4a7c9d2e1b3_add_worker_index_dispatch_receipts"
    )
    repair_migration = importlib.import_module(
        "migrations.versions."
        "a6c8e1f3b5d7_add_worker_dispatch_result_outbox"
    )
    assert repair_migration.down_revision == receipt_migration.revision

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        operations = Operations(
            MigrationContext.configure(connection)
        )
        monkeypatch.setattr(receipt_migration, "op", operations)
        monkeypatch.setattr(repair_migration, "op", operations)

        receipt_migration.upgrade()
        repair_migration.upgrade()
        repair_migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            item["name"]: item
            for item in inspector.get_columns(
                "knowledge_index_worker_dispatch_receipts"
            )
        }
        assert set(columns) == {
            column.name
            for column in KnowledgeIndexWorkerDispatchReceiptDB.__table__.columns
        }
        assert columns["state"]["nullable"] is False
        assert columns["result_digest"]["nullable"] is True
        assert columns["result_payload"]["nullable"] is True
        assert columns["completed_at_epoch_ms"]["nullable"] is True

        repair_migration.downgrade()
        assert {
            item["name"]
            for item in sa.inspect(connection).get_columns(
                "knowledge_index_worker_dispatch_receipts"
            )
        } == set(columns)

        receipt_migration.downgrade()
        assert (
            "knowledge_index_worker_dispatch_receipts"
            not in sa.inspect(connection).get_table_names()
        )
