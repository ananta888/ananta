from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from agent.db_models import KnowledgeIndexDB
from agent.repositories.organization_source_catalog_repository import (
    OrganizationSourceCatalogPersistenceError,
    OrganizationSourceCatalogUniqueRaceError,
    OrganizationSourceCatalogUnitOfWork,
    SourceCatalogPublishingAuthority,
    SqlOrganizationSourceCatalogRepository,
)
from agent.services.codecompass_artifact_manifest import (
    CodeCompassArtifactManifestProjector,
)
from agent.services.knowledge_index_retrieval_service import (
    KnowledgeIndexRetrievalService,
)


class _FirstResult:
    def __init__(self, value) -> None:
        self._value = value

    def first(self):
        return self._value


class _IndexSession:
    def __init__(self, index: KnowledgeIndexDB) -> None:
        self._index = index

    def exec(self, _statement):
        return _FirstResult(self._index)

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))


class _UniqueFlushSession:
    def flush(self):
        raise IntegrityError(
            "INSERT",
            {},
            RuntimeError("duplicate key value violates unique constraint"),
        )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _authority() -> SourceCatalogPublishingAuthority:
    return SourceCatalogPublishingAuthority(
        tenant_id="tenant-1",
        project_id="project-1",
        owner_id="operator-1",
        connection_id="connection-1",
        connector_type="registered_workspace",
        sensitivity="internal",
        source_revision_id="revision-1",
        revision_digest="1" * 64,
        source_manifest_digest="2" * 64,
        admission_receipt_id="receipt-1",
        admission_digest="3" * 64,
        knowledge_index_id="index-1",
        index_run_id="run-1",
        index_source_scope="repo_path",
        index_manifest_digest="4" * 64,
        policy_snapshot_digest="5" * 64,
        active_generation=3,
    )


def _snapshot(tmp_path):
    record = {
        "id": "record-hrm",
        "path": "docs/hrm.md",
        "kind": "document",
        "line_start": 1,
        "line_end": 4,
        "content": "Grounded HRM evidence",
    }
    index_content = (json.dumps(record) + "\n").encode("utf-8")
    manifest_content = b'{"coverage":{},"exclusions":[]}\n'
    (tmp_path / "index.jsonl").write_bytes(index_content)
    (tmp_path / "manifest.json").write_bytes(manifest_content)
    public_manifest = CodeCompassArtifactManifestProjector().project(
        knowledge_index_id="index-1",
        run_id="run-1",
        source_revision_id="revision-1",
        references=[
            {
                "role": "manifest",
                "filename": "manifest.json",
                "artifact_schema": "knowledge_index_manifest.v1",
                "media_type": "application/json",
                "size_bytes": len(manifest_content),
                "sha256": _sha256(manifest_content),
            },
            {
                "role": "index",
                "filename": "index.jsonl",
                "artifact_schema": "knowledge_index_records.v1",
                "media_type": "application/jsonl",
                "size_bytes": len(index_content),
                "sha256": _sha256(index_content),
            },
        ],
        coverage={},
        exclusions=(),
        status="completed",
    ).to_dict()
    index = KnowledgeIndexDB(
        id="index-1",
        latest_run_id="run-1",
        source_scope="repo_path",
        status="completed",
        output_dir=str(tmp_path),
        manifest_path=str(tmp_path / "manifest.json"),
        index_metadata={"artifact_manifest": public_manifest},
    )
    content = KnowledgeIndexRetrievalService()._record_text(record)
    binding = {
        "source_id": "SRC_0001",
        "record_file": "index.jsonl",
        "record_id": "record-hrm",
        "path": "docs/hrm.md",
        "line_start": 1,
        "line_end": 4,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    return index, binding, record


def test_bound_record_snapshot_is_hydratable_and_manifest_verified(tmp_path) -> None:
    index, binding, _record = _snapshot(tmp_path)
    repository = SqlOrganizationSourceCatalogRepository(_IndexSession(index))

    repository.verify_bound_records(
        authority=_authority(),
        record_bindings=[binding],
    )


def test_bound_record_mutation_between_query_and_publish_fails_closed(tmp_path) -> None:
    index, binding, record = _snapshot(tmp_path)
    record["content"] = "Mutated after query"
    (tmp_path / "index.jsonl").write_text(
        json.dumps(record, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    repository = SqlOrganizationSourceCatalogRepository(_IndexSession(index))

    with pytest.raises(
        OrganizationSourceCatalogPersistenceError,
        match="organization_source_catalog_output_record_mismatch",
    ):
        repository.verify_bound_records(
            authority=_authority(),
            record_bindings=[binding],
        )


def test_nonselected_output_mutation_breaks_manifest_snapshot(tmp_path) -> None:
    index, binding, _record = _snapshot(tmp_path)
    with (tmp_path / "index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"content":"unbound mutation","id":"other"}\n')
    repository = SqlOrganizationSourceCatalogRepository(_IndexSession(index))

    with pytest.raises(
        OrganizationSourceCatalogPersistenceError,
        match="organization_source_catalog_output_manifest_mismatch",
    ):
        repository.verify_bound_records(
            authority=_authority(),
            record_bindings=[binding],
        )


def test_uow_translates_only_unique_flush_failures_to_replay_signal() -> None:
    uow = OrganizationSourceCatalogUnitOfWork()
    uow.session = _UniqueFlushSession()  # type: ignore[assignment]

    with pytest.raises(OrganizationSourceCatalogUniqueRaceError):
        uow.flush()
