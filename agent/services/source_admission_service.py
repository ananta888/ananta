"""Revision-bound Hub admission for connector inventory and scan evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from ananta_contracts.source_control import MAX_SOURCE_ADMISSION_FILES

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")


class SourceAdmissionError(ValueError):
    pass


class SourceAdmissionState(str, Enum):
    admitted = "admitted"
    blocked = "blocked"


@dataclass(frozen=True)
class SourceAdmissionBudgets:
    max_files: int = MAX_SOURCE_ADMISSION_FILES
    max_total_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 16 * 1024 * 1024
    max_archive_expansion_ratio: float = 4.0
    allow_binary: bool = False
    allow_archives: bool = False
    allow_secrets: bool = False
    allow_prompt_injection: bool = False
    allowed_file_types: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if (
            self.max_files < 1
            or self.max_files > MAX_SOURCE_ADMISSION_FILES
            or self.max_total_bytes < 1
            or self.max_file_bytes < 1
            or self.max_archive_expansion_ratio < 1.0
        ):
            raise SourceAdmissionError("admission_budgets_invalid")


@dataclass(frozen=True)
class SourceInventoryEvidence:
    revision_digest: str
    manifest_digest: str
    file_count: int
    total_bytes: int
    largest_file_bytes: int
    archive_expansion_ratio: float
    file_type_counts: Mapping[str, int]
    symlink_count: int = 0
    hardlink_count: int = 0
    sparse_file_count: int = 0
    archive_count: int = 0
    binary_count: int = 0

    def __post_init__(self) -> None:
        _validate_digest("revision_digest", self.revision_digest)
        _validate_digest("manifest_digest", self.manifest_digest)
        counters = (
            self.file_count,
            self.total_bytes,
            self.largest_file_bytes,
            self.symlink_count,
            self.hardlink_count,
            self.sparse_file_count,
            self.archive_count,
            self.binary_count,
        )
        if any(not isinstance(value, int) or value < 0 for value in counters):
            raise SourceAdmissionError("inventory_counter_invalid")
        if self.archive_expansion_ratio < 0:
            raise SourceAdmissionError("archive_expansion_ratio_invalid")
        if sum(self.file_type_counts.values()) > self.file_count:
            raise SourceAdmissionError("file_type_counts_exceed_inventory")
        for file_type, count in self.file_type_counts.items():
            if not re.fullmatch(r"[a-z0-9_.+-]{1,64}", str(file_type)):
                raise SourceAdmissionError("file_type_invalid")
            if not isinstance(count, int) or count < 0:
                raise SourceAdmissionError("file_type_count_invalid")

    @property
    def evidence_digest(self) -> str:
        return _digest(
            {
                "revision_digest": self.revision_digest,
                "manifest_digest": self.manifest_digest,
                "file_count": self.file_count,
                "total_bytes": self.total_bytes,
                "largest_file_bytes": self.largest_file_bytes,
                "archive_expansion_ratio": self.archive_expansion_ratio,
                "file_type_counts": dict(sorted(self.file_type_counts.items())),
                "symlink_count": self.symlink_count,
                "hardlink_count": self.hardlink_count,
                "sparse_file_count": self.sparse_file_count,
                "archive_count": self.archive_count,
                "binary_count": self.binary_count,
            }
        )


@dataclass(frozen=True)
class SourceScanEvidence:
    revision_digest: str
    manifest_digest: str
    scanner_id: str
    scanner_version: str
    completed: bool
    secret_findings: int = 0
    injection_findings: int = 0
    rejected_type_findings: int = 0
    malformed_archive_findings: int = 0
    scan_error_count: int = 0

    def __post_init__(self) -> None:
        _validate_digest("revision_digest", self.revision_digest)
        _validate_digest("manifest_digest", self.manifest_digest)
        for name in ("scanner_id", "scanner_version"):
            if not _OPAQUE_ID.fullmatch(str(getattr(self, name) or "")):
                raise SourceAdmissionError(f"{name}_invalid")
        counters = (
            self.secret_findings,
            self.injection_findings,
            self.rejected_type_findings,
            self.malformed_archive_findings,
            self.scan_error_count,
        )
        if any(not isinstance(value, int) or value < 0 for value in counters):
            raise SourceAdmissionError("scan_counter_invalid")

    @property
    def evidence_digest(self) -> str:
        return _digest(
            {
                "revision_digest": self.revision_digest,
                "manifest_digest": self.manifest_digest,
                "scanner_id": self.scanner_id,
                "scanner_version": self.scanner_version,
                "completed": self.completed,
                "secret_findings": self.secret_findings,
                "injection_findings": self.injection_findings,
                "rejected_type_findings": self.rejected_type_findings,
                "malformed_archive_findings": self.malformed_archive_findings,
                "scan_error_count": self.scan_error_count,
            }
        )


@dataclass(frozen=True)
class SourceAdmissionDecision:
    authority: str
    tenant_id: str
    project_id: str
    source_revision_id: str
    revision_digest: str
    manifest_digest: str
    policy_digest: str
    state: SourceAdmissionState
    reason_codes: tuple[str, ...]
    inventory_evidence_digest: str
    scan_evidence_digest: str
    admission_digest: str


def evaluate_source_admission(
    *,
    tenant_id: str,
    project_id: str,
    source_revision_id: str,
    revision_digest: str,
    policy_digest: str,
    inventory: SourceInventoryEvidence,
    scan: SourceScanEvidence,
    budgets: SourceAdmissionBudgets,
) -> SourceAdmissionDecision:
    """Return the immutable Hub decision for one exact revision."""

    for name, value in (
        ("tenant_id", tenant_id),
        ("project_id", project_id),
        ("source_revision_id", source_revision_id),
    ):
        if not _OPAQUE_ID.fullmatch(str(value or "")):
            raise SourceAdmissionError(f"{name}_invalid")
    _validate_digest("revision_digest", revision_digest)
    _validate_digest("policy_digest", policy_digest)
    if inventory.revision_digest != revision_digest:
        raise SourceAdmissionError("inventory_revision_mismatch")
    if scan.revision_digest != revision_digest:
        raise SourceAdmissionError("scan_revision_mismatch")
    if scan.manifest_digest != inventory.manifest_digest:
        raise SourceAdmissionError("scan_manifest_mismatch")

    reasons: list[str] = []
    if not scan.completed or scan.scan_error_count:
        reasons.append("scan_incomplete")
    if inventory.file_count > budgets.max_files:
        reasons.append("file_count_budget_exceeded")
    if inventory.total_bytes > budgets.max_total_bytes:
        reasons.append("total_bytes_budget_exceeded")
    if inventory.largest_file_bytes > budgets.max_file_bytes:
        reasons.append("file_size_budget_exceeded")
    if inventory.archive_expansion_ratio > budgets.max_archive_expansion_ratio:
        reasons.append("archive_expansion_budget_exceeded")
    if inventory.symlink_count:
        reasons.append("symlink_forbidden")
    if inventory.hardlink_count:
        reasons.append("hardlink_forbidden")
    if inventory.sparse_file_count:
        reasons.append("sparse_file_forbidden")
    if inventory.archive_count and not budgets.allow_archives:
        reasons.append("archive_forbidden")
    if inventory.binary_count and not budgets.allow_binary:
        reasons.append("binary_forbidden")
    if scan.secret_findings and not budgets.allow_secrets:
        reasons.append("secret_detected")
    if scan.injection_findings and not budgets.allow_prompt_injection:
        reasons.append("prompt_injection_detected")
    if scan.rejected_type_findings:
        reasons.append("rejected_file_type")
    if scan.malformed_archive_findings:
        reasons.append("malformed_archive")
    if budgets.allowed_file_types:
        unsupported = set(inventory.file_type_counts) - budgets.allowed_file_types
        if unsupported:
            reasons.append("unsupported_file_type")

    reason_codes = tuple(sorted(set(reasons)))
    state = (
        SourceAdmissionState.blocked
        if reason_codes
        else SourceAdmissionState.admitted
    )
    decision_payload = {
        "authority": "hub",
        "tenant_id": tenant_id,
        "project_id": project_id,
        "source_revision_id": source_revision_id,
        "revision_digest": revision_digest,
        "manifest_digest": inventory.manifest_digest,
        "policy_digest": policy_digest,
        "state": state.value,
        "reason_codes": list(reason_codes),
        "inventory_evidence_digest": inventory.evidence_digest,
        "scan_evidence_digest": scan.evidence_digest,
    }
    return SourceAdmissionDecision(
        **{
            **decision_payload,
            "state": state,
            "reason_codes": reason_codes,
        },
        admission_digest=_digest(decision_payload),
    )


def _validate_digest(name: str, value: str) -> None:
    if not _SHA256.fullmatch(str(value or "")):
        raise SourceAdmissionError(f"{name}_invalid")


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
