from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from agent.db_models.source_admission_receipt import SourceAdmissionReceiptDB
from agent.repositories.business_controlling_source_admission import SqlControllingSourceAdmission


def _receipt(*, state: str = "admitted") -> SourceAdmissionReceiptDB:
    return SourceAdmissionReceiptDB(
        receipt_id="receipt_" + "1" * 61,
        tenant_id="tenant-a",
        project_id="project-a",
        source_revision_id="srev_" + "2" * 64,
        decision_state=state,
        reason_codes=[],
        revision_digest="3" * 64,
        manifest_digest="4" * 64,
        policy_digest="5" * 64,
        inventory_evidence_digest="6" * 64,
        scan_evidence_digest="7" * 64,
        admission_digest="8" * 64,
        file_count=1,
        total_bytes=10,
        largest_file_bytes=10,
        archive_expansion_ratio=0,
        symlink_count=0,
        hardlink_count=0,
        sparse_file_count=0,
        archive_count=0,
        binary_count=0,
        secret_findings=0,
        injection_findings=0,
        rejected_type_findings=0,
        malformed_archive_findings=0,
        scan_error_count=0,
        evaluated_at_epoch=1,
        persisted_at_epoch=1,
    )


def test_sql_admission_adapter_requires_exact_admitted_scope(tmp_path) -> None:
    database = create_engine(f"sqlite:///{tmp_path / 'controlling-admission.db'}")
    SQLModel.metadata.create_all(database)
    receipt = _receipt()
    source_revision_id = receipt.source_revision_id
    revision_digest = receipt.revision_digest
    with Session(database) as session:
        session.add(receipt)
        session.commit()
    adapter = SqlControllingSourceAdmission(database)
    assert adapter.is_admitted(
        tenant_id="tenant-a",
        project_id="project-a",
        source_revision_id=source_revision_id,
        revision_digest=revision_digest,
    )
    assert not adapter.is_admitted(
        tenant_id="tenant-b",
        project_id="project-a",
        source_revision_id=source_revision_id,
        revision_digest=revision_digest,
    )
