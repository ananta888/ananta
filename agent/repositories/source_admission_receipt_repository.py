"""Immutable SQL persistence for content-free source admission receipts."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Protocol, runtime_checkable

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_admission_receipt import SourceAdmissionReceiptDB
from agent.db_models.source_control import SourceRevisionDB

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REASON_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_MAX_SCOPE_ID_LENGTH = 128
_MAX_REVISION_ID_LENGTH = 69


class SourceAdmissionReceiptPersistenceError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SourceAdmissionCounters:
    file_count: int
    total_bytes: int
    largest_file_bytes: int
    archive_expansion_ratio: float
    symlink_count: int = 0
    hardlink_count: int = 0
    sparse_file_count: int = 0
    archive_count: int = 0
    binary_count: int = 0
    secret_findings: int = 0
    injection_findings: int = 0
    rejected_type_findings: int = 0
    malformed_archive_findings: int = 0
    scan_error_count: int = 0

    def __post_init__(self) -> None:
        for field_definition in fields(self):
            if field_definition.name == "archive_expansion_ratio":
                continue
            value = getattr(self, field_definition.name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SourceAdmissionReceiptPersistenceError(
                    "source_admission_receipt_counter_invalid"
                )
        if (
            isinstance(self.archive_expansion_ratio, bool)
            or not isinstance(self.archive_expansion_ratio, (int, float))
            or not math.isfinite(float(self.archive_expansion_ratio))
            or self.archive_expansion_ratio < 0
        ):
            raise SourceAdmissionReceiptPersistenceError(
                "source_admission_receipt_expansion_ratio_invalid"
            )


@dataclass(frozen=True)
class SourceAdmissionReceiptDraft:
    tenant_id: str
    project_id: str
    source_revision_id: str
    decision_state: str
    reason_codes: tuple[str, ...]
    revision_digest: str
    manifest_digest: str
    policy_digest: str
    inventory_evidence_digest: str
    scan_evidence_digest: str
    admission_digest: str
    counters: SourceAdmissionCounters
    evaluated_at_epoch: float

    def __post_init__(self) -> None:
        for name, max_length in (
            ("tenant_id", _MAX_SCOPE_ID_LENGTH),
            ("project_id", _MAX_SCOPE_ID_LENGTH),
            ("source_revision_id", _MAX_REVISION_ID_LENGTH),
        ):
            value = str(getattr(self, name) or "").strip()
            if not value or len(value) > max_length:
                raise SourceAdmissionReceiptPersistenceError(
                    f"source_admission_receipt_{name}_invalid"
                )
            object.__setattr__(self, name, value)

        raw_state = getattr(self.decision_state, "value", self.decision_state)
        state = str(raw_state)
        if state not in {"admitted", "blocked"}:
            raise SourceAdmissionReceiptPersistenceError(
                "source_admission_receipt_decision_invalid"
            )
        object.__setattr__(self, "decision_state", state)

        reasons = tuple(str(reason) for reason in self.reason_codes)
        if reasons != tuple(sorted(set(reasons))) or any(
            _REASON_CODE.fullmatch(reason) is None for reason in reasons
        ):
            raise SourceAdmissionReceiptPersistenceError(
                "source_admission_receipt_reasons_invalid"
            )
        if (state == "blocked") != bool(reasons):
            raise SourceAdmissionReceiptPersistenceError(
                "source_admission_receipt_reasons_mismatch"
            )
        object.__setattr__(self, "reason_codes", reasons)

        for name in (
            "revision_digest",
            "manifest_digest",
            "policy_digest",
            "inventory_evidence_digest",
            "scan_evidence_digest",
            "admission_digest",
        ):
            if _SHA256.fullmatch(str(getattr(self, name) or "")) is None:
                raise SourceAdmissionReceiptPersistenceError(
                    f"source_admission_receipt_{name}_invalid"
                )
        if (
            isinstance(self.evaluated_at_epoch, bool)
            or not isinstance(self.evaluated_at_epoch, (int, float))
            or not math.isfinite(float(self.evaluated_at_epoch))
            or self.evaluated_at_epoch < 0
        ):
            raise SourceAdmissionReceiptPersistenceError(
                "source_admission_receipt_timestamp_invalid"
            )


@dataclass(frozen=True)
class SourceAdmissionReceiptRecord(SourceAdmissionReceiptDraft):
    receipt_id: str
    persisted_at_epoch: float

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.receipt_id != f"sar_{self.admission_digest}":
            raise SourceAdmissionReceiptPersistenceError(
                "source_admission_receipt_identity_invalid"
            )
        if (
            isinstance(self.persisted_at_epoch, bool)
            or not isinstance(self.persisted_at_epoch, (int, float))
            or not math.isfinite(float(self.persisted_at_epoch))
            or self.persisted_at_epoch < 0
        ):
            raise SourceAdmissionReceiptPersistenceError(
                "source_admission_receipt_timestamp_invalid"
            )

    def as_draft(self) -> SourceAdmissionReceiptDraft:
        return SourceAdmissionReceiptDraft(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            source_revision_id=self.source_revision_id,
            decision_state=self.decision_state,
            reason_codes=self.reason_codes,
            revision_digest=self.revision_digest,
            manifest_digest=self.manifest_digest,
            policy_digest=self.policy_digest,
            inventory_evidence_digest=self.inventory_evidence_digest,
            scan_evidence_digest=self.scan_evidence_digest,
            admission_digest=self.admission_digest,
            counters=self.counters,
            evaluated_at_epoch=self.evaluated_at_epoch,
        )


@runtime_checkable
class SourceAdmissionReceiptPort(Protocol):
    def append(
        self,
        receipt: SourceAdmissionReceiptDraft,
    ) -> SourceAdmissionReceiptRecord: ...

    def get(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        receipt_id: str,
    ) -> SourceAdmissionReceiptRecord | None: ...

    def get_by_admission_digest(
        self,
        *,
        tenant_id: str,
        project_id: str,
        admission_digest: str,
    ) -> SourceAdmissionReceiptRecord | None: ...


class SQLSourceAdmissionReceiptRepository:
    """Append-only adapter; intentionally exposes no update or delete operation."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine = engine
        self._clock = clock

    def append(
        self,
        receipt: SourceAdmissionReceiptDraft,
    ) -> SourceAdmissionReceiptRecord:
        receipt_id = f"sar_{receipt.admission_digest}"
        with Session(self._engine) as db:
            revision = db.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.source_revision_id == receipt.source_revision_id,
                    SourceRevisionDB.tenant_id == receipt.tenant_id,
                    SourceRevisionDB.project_id == receipt.project_id,
                )
            ).first()
            if revision is None:
                raise SourceAdmissionReceiptPersistenceError(
                    "source_admission_revision_not_found"
                )
            if (
                revision.revision_digest != receipt.revision_digest
                or revision.content_manifest_digest != receipt.manifest_digest
            ):
                raise SourceAdmissionReceiptPersistenceError(
                    "source_admission_revision_digest_mismatch"
                )

            existing = db.get(SourceAdmissionReceiptDB, receipt_id)
            if existing is not None:
                return self._idempotent_or_conflict(existing, receipt)

            persisted_at_epoch = float(self._clock())
            row = SourceAdmissionReceiptDB(
                receipt_id=receipt_id,
                tenant_id=receipt.tenant_id,
                project_id=receipt.project_id,
                source_revision_id=receipt.source_revision_id,
                decision_state=receipt.decision_state,
                reason_codes=list(receipt.reason_codes),
                revision_digest=receipt.revision_digest,
                manifest_digest=receipt.manifest_digest,
                policy_digest=receipt.policy_digest,
                inventory_evidence_digest=receipt.inventory_evidence_digest,
                scan_evidence_digest=receipt.scan_evidence_digest,
                admission_digest=receipt.admission_digest,
                **self._counter_values(receipt.counters),
                evaluated_at_epoch=float(receipt.evaluated_at_epoch),
                persisted_at_epoch=persisted_at_epoch,
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                concurrent = db.get(SourceAdmissionReceiptDB, receipt_id)
                if concurrent is not None:
                    return self._idempotent_or_conflict(concurrent, receipt)
                raise SourceAdmissionReceiptPersistenceError(
                    "source_admission_receipt_identity_conflict"
                ) from None
            db.refresh(row)
            return self._record(row)

    def get(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        receipt_id: str,
    ) -> SourceAdmissionReceiptRecord | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceAdmissionReceiptDB).where(
                    SourceAdmissionReceiptDB.receipt_id == receipt_id,
                    SourceAdmissionReceiptDB.tenant_id == tenant_id,
                    SourceAdmissionReceiptDB.project_id == project_id,
                    SourceAdmissionReceiptDB.source_revision_id
                    == source_revision_id,
                )
            ).first()
            return None if row is None else self._record(row)

    def get_by_admission_digest(
        self,
        *,
        tenant_id: str,
        project_id: str,
        admission_digest: str,
    ) -> SourceAdmissionReceiptRecord | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(SourceAdmissionReceiptDB).where(
                    SourceAdmissionReceiptDB.tenant_id == tenant_id,
                    SourceAdmissionReceiptDB.project_id == project_id,
                    SourceAdmissionReceiptDB.admission_digest == admission_digest,
                )
            ).first()
            return None if row is None else self._record(row)

    @classmethod
    def _idempotent_or_conflict(
        cls,
        row: SourceAdmissionReceiptDB,
        receipt: SourceAdmissionReceiptDraft,
    ) -> SourceAdmissionReceiptRecord:
        record = cls._record(row)
        if record.as_draft() != receipt:
            raise SourceAdmissionReceiptPersistenceError(
                "source_admission_receipt_identity_conflict"
            )
        return record

    @staticmethod
    def _counter_values(counters: SourceAdmissionCounters) -> dict[str, object]:
        return {
            field_definition.name: getattr(counters, field_definition.name)
            for field_definition in fields(counters)
        }

    @staticmethod
    def _record(row: SourceAdmissionReceiptDB) -> SourceAdmissionReceiptRecord:
        counters = SourceAdmissionCounters(
            file_count=row.file_count,
            total_bytes=row.total_bytes,
            largest_file_bytes=row.largest_file_bytes,
            archive_expansion_ratio=row.archive_expansion_ratio,
            symlink_count=row.symlink_count,
            hardlink_count=row.hardlink_count,
            sparse_file_count=row.sparse_file_count,
            archive_count=row.archive_count,
            binary_count=row.binary_count,
            secret_findings=row.secret_findings,
            injection_findings=row.injection_findings,
            rejected_type_findings=row.rejected_type_findings,
            malformed_archive_findings=row.malformed_archive_findings,
            scan_error_count=row.scan_error_count,
        )
        return SourceAdmissionReceiptRecord(
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            source_revision_id=row.source_revision_id,
            decision_state=row.decision_state,
            reason_codes=tuple(row.reason_codes),
            revision_digest=row.revision_digest,
            manifest_digest=row.manifest_digest,
            policy_digest=row.policy_digest,
            inventory_evidence_digest=row.inventory_evidence_digest,
            scan_evidence_digest=row.scan_evidence_digest,
            admission_digest=row.admission_digest,
            counters=counters,
            evaluated_at_epoch=row.evaluated_at_epoch,
            receipt_id=row.receipt_id,
            persisted_at_epoch=row.persisted_at_epoch,
        )


__all__ = [
    "SQLSourceAdmissionReceiptRepository",
    "SourceAdmissionCounters",
    "SourceAdmissionReceiptDraft",
    "SourceAdmissionReceiptPersistenceError",
    "SourceAdmissionReceiptPort",
    "SourceAdmissionReceiptRecord",
]
