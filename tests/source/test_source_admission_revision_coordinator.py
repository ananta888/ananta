from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlmodel import SQLModel

from agent.db_models.source_control import SourceRevisionDB
from agent.repositories.source_admission_receipt_repository import (
    SQLSourceAdmissionReceiptRepository,
    SourceAdmissionCounters,
    SourceAdmissionReceiptDraft,
    SourceAdmissionReceiptPersistenceError,
)
from agent.services.source_admission_revision_coordinator import (
    SourceAdmissionRevisionCoordinator,
    SourceAdmissionRevisionRequest,
)
from agent.services.source_admission_service import (
    SourceAdmissionBudgets,
    SourceInventoryEvidence,
    SourceScanEvidence,
)
from agent.services.source_filesystem_scanner import SourceFilesystemScanResult
from agent.sources.registered_workspace_connector import (
    RegisteredWorkspace,
    WorkspaceInventoryManifest,
)


class _Scanner:
    def __init__(self, result: SourceFilesystemScanResult) -> None:
        self.result = result

    def scan(self, **_: object) -> SourceFilesystemScanResult:
        return self.result


class _RevisionRepository:
    def __init__(self) -> None:
        self.revisions: dict[str, object] = {}

    def append_revision(self, contract: object) -> object:
        revision_id = getattr(contract, "source_revision_id")
        existing = self.revisions.get(revision_id)
        if existing is not None:
            assert existing == contract
            return existing
        self.revisions[revision_id] = contract
        return contract


class _ReceiptRepository:
    def __init__(self) -> None:
        self.receipts: dict[str, object] = {}
        self.append_count = 0

    def append(self, receipt: SourceAdmissionReceiptDraft) -> object:
        self.append_count += 1
        values = {field.name: getattr(receipt, field.name) for field in fields(receipt)}
        record = SimpleNamespace(
            receipt_id=f"sar_{receipt.admission_digest}",
            persisted_at_epoch=receipt.evaluated_at_epoch,
            **values,
        )
        self.receipts[receipt.admission_digest] = record
        return record

    def get(self, *_: object) -> object | None:
        return None

    def get_by_admission_digest(
        self, tenant_id: str, project_id: str, admission_digest: str
    ) -> object | None:
        receipt = self.receipts.get(admission_digest)
        if receipt is None:
            return None
        assert receipt.tenant_id == tenant_id
        assert receipt.project_id == project_id
        return receipt


def _scan_result(*, secret_findings: int = 0) -> SourceFilesystemScanResult:
    revision_digest = "a" * 64
    manifest_digest = "b" * 64
    return SourceFilesystemScanResult(
        inventory=SourceInventoryEvidence(
            revision_digest=revision_digest,
            manifest_digest=manifest_digest,
            file_count=1,
            total_bytes=12,
            largest_file_bytes=12,
            archive_expansion_ratio=0.0,
            file_type_counts={"txt": 1},
            symlink_count=0,
            hardlink_count=0,
            sparse_file_count=0,
            archive_count=0,
            binary_count=0,
        ),
        scan=SourceScanEvidence(
            revision_digest=revision_digest,
            manifest_digest=manifest_digest,
            scanner_id="production-filesystem",
            scanner_version="1",
            completed=True,
            secret_findings=secret_findings,
            injection_findings=0,
            rejected_type_findings=0,
            malformed_archive_findings=0,
            scan_error_count=0,
        ),
    )


def _request(tmp_path: Path) -> SourceAdmissionRevisionRequest:
    workspace = RegisteredWorkspace(
        workspace_id="workspace-1",
        tenant_id="tenant-1",
        project_id="project-1",
        root=tmp_path,
        enabled=True,
        read_only=True,
        owner_id="owner-1",
    )
    snapshot = WorkspaceInventoryManifest(
        workspace_id=workspace.workspace_id,
        relative_root=".",
        entries=(),
        total_bytes=0,
        manifest_digest="b" * 64,
        revision_digest="a" * 64,
    )
    return SourceAdmissionRevisionRequest(
        connection_id="conn_" + "1" * 64,
        tenant_id=workspace.tenant_id,
        project_id=workspace.project_id,
        owner_id=workspace.owner_id,
        connector_type="registered_workspace",
        sensitivity="internal",
        policy_digest="c" * 64,
        workspace=workspace,
        snapshot=snapshot,
        captured_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def test_coordinator_persists_admitted_revision_and_content_free_receipt(
    tmp_path: Path,
) -> None:
    revisions = _RevisionRepository()
    receipts = _ReceiptRepository()
    coordinator = SourceAdmissionRevisionCoordinator(
        scanner=_Scanner(_scan_result()),
        revision_repository=revisions,
        receipt_repository=receipts,
        budgets=SourceAdmissionBudgets(allowed_file_types=frozenset({"txt"})),
        clock=lambda: 123.0,
    )

    first = coordinator.admit(_request(tmp_path))
    second = coordinator.admit(_request(tmp_path))

    assert first.decision.state.value == "admitted"
    assert first.receipt.decision_state == "admitted"
    assert first.receipt.admission_digest == first.decision.admission_digest
    assert first.receipt is second.receipt
    assert receipts.append_count == 1
    assert not hasattr(first.receipt, "path")
    assert not hasattr(first.receipt, "content")


def test_coordinator_persists_blocked_revision_and_receipt(tmp_path: Path) -> None:
    receipts = _ReceiptRepository()
    coordinator = SourceAdmissionRevisionCoordinator(
        scanner=_Scanner(_scan_result(secret_findings=1)),
        revision_repository=_RevisionRepository(),
        receipt_repository=receipts,
        budgets=SourceAdmissionBudgets(allowed_file_types=frozenset({"txt"})),
        clock=lambda: 456.0,
    )

    result = coordinator.admit(_request(tmp_path))

    assert result.decision.state.value == "blocked"
    assert result.receipt.decision_state == "blocked"
    assert result.receipt.counters.secret_findings == 1


def _receipt_draft(**overrides: object) -> SourceAdmissionReceiptDraft:
    base = SourceAdmissionReceiptDraft(
        tenant_id="tenant-1",
        project_id="project-1",
        source_revision_id="revision-1",
        decision_state="blocked",
        reason_codes=("source_secret_detected",),
        revision_digest="a" * 64,
        manifest_digest="b" * 64,
        policy_digest="c" * 64,
        inventory_evidence_digest="d" * 64,
        scan_evidence_digest="e" * 64,
        admission_digest="f" * 64,
        counters=SourceAdmissionCounters(
            file_count=1,
            total_bytes=12,
            largest_file_bytes=12,
            archive_expansion_ratio=0.0,
            symlink_count=0,
            hardlink_count=0,
            sparse_file_count=0,
            archive_count=0,
            binary_count=0,
            secret_findings=1,
            injection_findings=0,
            rejected_type_findings=0,
            malformed_archive_findings=0,
            scan_error_count=0,
        ),
        evaluated_at_epoch=123.0,
    )
    return replace(base, **overrides)


def test_sql_receipt_repository_is_scoped_append_only_and_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            SourceRevisionDB.__table__.insert().values(
                source_revision_id="revision-1",
                    connection_id="conn_" + "1" * 64,
                tenant_id="tenant-1",
                project_id="project-1",
                owner_id="owner-1",
                connector_type="registered_workspace",
                sensitivity="internal",
                revision_token="workspace-manifest:" + "b" * 64,
                revision_digest="a" * 64,
                content_manifest_id="manifest_" + "b" * 64,
                content_manifest_digest="b" * 64,
                admission_state="blocked",
                captured_at_epoch=100.0,
            )
        )
    repository = SQLSourceAdmissionReceiptRepository(engine, clock=lambda: 124.0)
    draft = _receipt_draft()

    first = repository.append(draft)
    second = repository.append(draft)

    assert first == second
    assert first.receipt_id == "sar_" + "f" * 64
    assert (
        repository.get(
            tenant_id="tenant-1",
            project_id="project-1",
            source_revision_id="revision-1",
            receipt_id=first.receipt_id,
        )
        == first
    )
    assert (
        repository.get(
            tenant_id="tenant-2",
            project_id="project-1",
            source_revision_id="revision-1",
            receipt_id=first.receipt_id,
        )
        is None
    )

    try:
        repository.append(_receipt_draft(reason_codes=("different",)))
    except SourceAdmissionReceiptPersistenceError as exc:
        assert exc.reason_code == "source_admission_receipt_identity_conflict"
    else:
        raise AssertionError("conflicting append must fail closed")
