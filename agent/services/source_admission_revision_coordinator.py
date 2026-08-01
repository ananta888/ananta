"""Application service for immutable source admission and revision recording.

The coordinator deliberately depends on narrow scanner and persistence ports.  It
does not know how a source is registered or later consumed by planners/workers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
import time
from typing import Callable, Protocol

from ananta_contracts.source_control import SourceRevision, derive_source_revision_id

from agent.repositories.source_admission_receipt_repository import (
    SourceAdmissionCounters,
    SourceAdmissionReceiptDraft,
    SourceAdmissionReceiptPort,
    SourceAdmissionReceiptRecord,
)
from agent.services.source_admission_service import (
    SourceAdmissionBudgets,
    SourceAdmissionDecision,
    evaluate_source_admission,
)
from agent.services.source_control_persistence import SourceRevisionRecord
from agent.services.source_filesystem_scanner import SourceFilesystemScanResult
from agent.sources.registered_workspace_connector import (
    RegisteredWorkspace,
    WorkspaceInventoryManifest,
)


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class SourceEvidenceScannerPort(Protocol):
    """Produces bounded, content-free evidence for one materialized snapshot."""

    def scan(
        self,
        *,
        workspace: RegisteredWorkspace,
        snapshot: WorkspaceInventoryManifest,
        budgets: SourceAdmissionBudgets,
    ) -> SourceFilesystemScanResult: ...


class SourceRevisionAppendPort(Protocol):
    """Append-only persistence boundary already implemented by source control."""

    def append_revision(self, contract: SourceRevision) -> SourceRevisionRecord: ...


class SourceAdmissionRevisionError(RuntimeError):
    """Content-free coordinator failure suitable for audit/event propagation."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SourceAdmissionRevisionRequest:
    connection_id: str
    tenant_id: str
    project_id: str
    owner_id: str
    connector_type: str
    sensitivity: str
    policy_digest: str
    workspace: RegisteredWorkspace
    snapshot: WorkspaceInventoryManifest
    captured_at: datetime


@dataclass(frozen=True)
class SourceAdmissionRevisionResult:
    decision: SourceAdmissionDecision
    scan_result: SourceFilesystemScanResult
    revision: SourceRevisionRecord
    receipt: SourceAdmissionReceiptRecord


class SourceAdmissionRevisionCoordinator:
    """Scans, decides, and records one immutable source revision admission."""

    def __init__(
        self,
        *,
        scanner: SourceEvidenceScannerPort,
        revision_repository: SourceRevisionAppendPort,
        receipt_repository: SourceAdmissionReceiptPort,
        budgets: SourceAdmissionBudgets | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._scanner = scanner
        self._revision_repository = revision_repository
        self._receipt_repository = receipt_repository
        self._budgets = budgets or SourceAdmissionBudgets()
        self._clock = clock

    def admit(
        self, request: SourceAdmissionRevisionRequest
    ) -> SourceAdmissionRevisionResult:
        self._validate_request(request)

        scan_result = self._scanner.scan(
            workspace=request.workspace,
            snapshot=request.snapshot,
            budgets=self._budgets,
        )
        inventory = scan_result.inventory
        scan = scan_result.scan
        source_revision_id = derive_source_revision_id(
            connection_id=request.connection_id,
            revision_digest=inventory.revision_digest,
        )

        decision = evaluate_source_admission(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            source_revision_id=source_revision_id,
            revision_digest=inventory.revision_digest,
            policy_digest=request.policy_digest.lower(),
            inventory=inventory,
            scan=scan,
            budgets=self._budgets,
        )

        revision_contract = SourceRevision.create(
            connection_id=request.connection_id,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            owner_id=request.owner_id,
            connector_type=request.connector_type,
            sensitivity=request.sensitivity,
            revision_token=f"workspace-manifest:{inventory.manifest_digest}",
            revision_digest=inventory.revision_digest,
            content_manifest_id=f"manifest_{inventory.manifest_digest}",
            content_manifest_digest=inventory.manifest_digest,
            admission_state=decision.state.value,
            captured_at=request.captured_at,
        )
        revision = self._revision_repository.append_revision(revision_contract)

        existing_receipt = self._receipt_repository.get_by_admission_digest(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            admission_digest=decision.admission_digest,
        )
        if existing_receipt is not None:
            self._validate_existing_receipt(existing_receipt, decision)
            receipt = existing_receipt
        else:
            receipt = self._receipt_repository.append(
                SourceAdmissionReceiptDraft(
                    tenant_id=request.tenant_id,
                    project_id=request.project_id,
                    source_revision_id=source_revision_id,
                    decision_state=decision.state.value,
                    reason_codes=tuple(decision.reason_codes),
                    revision_digest=inventory.revision_digest,
                    manifest_digest=inventory.manifest_digest,
                    policy_digest=request.policy_digest.lower(),
                    inventory_evidence_digest=decision.inventory_evidence_digest,
                    scan_evidence_digest=decision.scan_evidence_digest,
                    admission_digest=decision.admission_digest,
                    counters=SourceAdmissionCounters(
                        file_count=inventory.file_count,
                        total_bytes=inventory.total_bytes,
                        largest_file_bytes=inventory.largest_file_bytes,
                        archive_expansion_ratio=inventory.archive_expansion_ratio,
                        symlink_count=inventory.symlink_count,
                        hardlink_count=inventory.hardlink_count,
                        sparse_file_count=inventory.sparse_file_count,
                        archive_count=inventory.archive_count,
                        binary_count=inventory.binary_count,
                        secret_findings=scan.secret_findings,
                        injection_findings=scan.injection_findings,
                        rejected_type_findings=scan.rejected_type_findings,
                        malformed_archive_findings=scan.malformed_archive_findings,
                        scan_error_count=scan.scan_error_count,
                    ),
                    evaluated_at_epoch=float(self._clock()),
                )
            )

        return SourceAdmissionRevisionResult(
            decision=decision,
            scan_result=scan_result,
            revision=revision,
            receipt=receipt,
        )

    @staticmethod
    def _validate_request(request: SourceAdmissionRevisionRequest) -> None:
        if not all(
            (
                request.connection_id,
                request.tenant_id,
                request.project_id,
                request.owner_id,
                request.connector_type,
                request.sensitivity,
            )
        ):
            raise SourceAdmissionRevisionError("source_admission_scope_missing")
        if not _SHA256_RE.fullmatch(request.policy_digest):
            raise SourceAdmissionRevisionError("source_admission_policy_digest_invalid")
        if request.captured_at.tzinfo is None:
            raise SourceAdmissionRevisionError("source_revision_capture_time_naive")
        workspace = request.workspace
        if (
            workspace.tenant_id != request.tenant_id
            or workspace.project_id != request.project_id
            or workspace.owner_id != request.owner_id
            or request.snapshot.workspace_id != workspace.workspace_id
        ):
            raise SourceAdmissionRevisionError("source_admission_scope_mismatch")

    @staticmethod
    def _validate_existing_receipt(
        receipt: SourceAdmissionReceiptRecord,
        decision: SourceAdmissionDecision,
    ) -> None:
        if (
            receipt.source_revision_id != decision.source_revision_id
            or receipt.decision_state != decision.state.value
            or tuple(receipt.reason_codes) != tuple(decision.reason_codes)
            or receipt.revision_digest != decision.revision_digest
            or receipt.manifest_digest != decision.manifest_digest
            or receipt.policy_digest != decision.policy_digest
            or receipt.inventory_evidence_digest
            != decision.inventory_evidence_digest
            or receipt.scan_evidence_digest != decision.scan_evidence_digest
            or receipt.admission_digest != decision.admission_digest
        ):
            raise SourceAdmissionRevisionError(
                "source_admission_receipt_identity_conflict"
            )


__all__ = [
    "SourceAdmissionRevisionCoordinator",
    "SourceAdmissionRevisionError",
    "SourceAdmissionRevisionRequest",
    "SourceAdmissionRevisionResult",
    "SourceEvidenceScannerPort",
    "SourceRevisionAppendPort",
]
