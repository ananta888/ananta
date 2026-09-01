from __future__ import annotations

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

import agent.db_models  # noqa: F401 - registers SQLModel metadata
from agent.adapters.spreadsheet_mock_execution_adapter import (
    DeterministicSpreadsheetMockExecutionAdapter,
)
from agent.repositories.spreadsheet_document_repository import (
    SqlSpreadsheetDocumentRepository,
)
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from tests.spreadsheet_studio.helpers import proposal, snapshot


def _repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'spreadsheet-repository.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    return engine, SqlSpreadsheetDocumentRepository(db_engine=engine)


def test_sql_repository_persists_immutable_versions_and_replay(tmp_path) -> None:
    engine, repository = _repository(tmp_path)
    studio = SpreadsheetSagaService(
        repository,
        policy=SpreadsheetPolicy(enabled=True, mode="mock", automatic_promotion_enabled=True),
        executor=DeterministicSpreadsheetMockExecutionAdapter(),
    )
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="owner-a",
        title="Budget",
        snapshot=snapshot(),
        document_id="document-a",
    )
    result = studio.execute_proposal(
        tenant_id="tenant-a",
        principal_id="owner-a",
        proposal=proposal(document),
    )

    assert result["promoted_version"] == 2
    reopened = SqlSpreadsheetDocumentRepository(db_engine=engine)
    assert reopened.get_version("tenant-a", "document-a", 1)["snapshot"]["sheets"][0]["cells"][0]["value"] == 1
    assert reopened.get_version("tenant-a", "document-a", 2)["snapshot"]["sheets"][0]["cells"][0]["value"] == 42
    assert [row["version"] for row in reopened.list_versions("tenant-a", "document-a")["items"]] == [2, 1]
    assert (
        studio.execute_proposal(
            tenant_id="tenant-a",
            principal_id="owner-a",
            proposal=proposal(document),
        )["replayed"]
        is True
    )


def test_sql_repository_fails_closed_on_tenant_scope_and_payload_tampering(tmp_path) -> None:
    engine, repository = _repository(tmp_path)
    repository.create_document(
        "tenant-a",
        {
            "schema": "ananta.spreadsheet-document-version.v1",
            "document_id": "document-a",
            "owner_id": "owner-a",
            "snapshot_digest": "a" * 64,
            "snapshot": {},
            "state": "published",
        },
    )

    try:
        repository.get_document("tenant-b", "document-a")
    except KeyError as exc:
        assert exc.args == ("spreadsheet_document_not_found",)
    else:
        raise AssertionError("cross-tenant document lookup was accepted")

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE spreadsheet_document_versions SET payload_json='{}' "
                "WHERE tenant_id='tenant-a' AND document_id='document-a' AND version=1"
            )
        )
    try:
        repository.get_document("tenant-a", "document-a")
    except RuntimeError as exc:
        assert str(exc) == "spreadsheet_document_payload_integrity_failed"
    else:
        raise AssertionError("tampered document payload was accepted")


def test_spreadsheet_document_migration_upgrade_and_downgrade(monkeypatch) -> None:
    migration = importlib.import_module("migrations.versions.b9d1f3a5c7e0_add_spreadsheet_document_persistence")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert {
            "spreadsheet_documents",
            "spreadsheet_document_versions",
            "spreadsheet_proposal_results",
        } <= set(inspect(connection).get_table_names())
        migration.downgrade()
        assert not {
            "spreadsheet_documents",
            "spreadsheet_document_versions",
            "spreadsheet_proposal_results",
        } & set(inspect(connection).get_table_names())
