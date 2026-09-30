"""Speech adapter registry records, receipts, errors and export ports.

Value types and narrow ports shared by the registry, its lineage adapter and
the export composition (ISP: export and lineage ports stay separate)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


class SpeechAdapterRegistryError(ValueError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


class SpeechAdapterNotFound(SpeechAdapterRegistryError):
    pass


class SpeechAdapterVersionConflict(SpeechAdapterRegistryError):
    pass


@dataclass
class SpeechAdapterRecord:
    adapter_id: str
    version: str
    tenant_id: str
    owner_subject: str
    pair_id: str
    direction: str
    speaker_digest: str
    scope_digest: str
    base_model_id: str
    base_model_digest: str
    backend: str
    backend_digest: str
    dataset_digest: str
    split_digest: str
    evaluation_report_digest: str
    evaluation_policy_version: str
    evaluation_passed: bool
    evaluation_approval_eligible: bool
    consent_digest: str
    consent_expires_at_ms: int
    artifact_ref: str
    artifact_sha256: str
    artifact_size_bytes: int
    expires_at_ms: int
    status: str = "evaluated"
    registry_version: int = 1
    approved_by_digest: str | None = None
    approval_reason_code: str | None = None
    approved_at_ms: int | None = None
    revoked_at_ms: int | None = None
    deprecated_at_ms: int | None = None
    expired_at_ms: int | None = None
    rollback_of_adapter_id: str | None = None
    created_at_ms: int = 0
    updated_at_ms: int = 0
    lineage: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> dict[str, Any]:
        """Content-free API model without server paths, keys or owner identity."""

        return {
            "adapter_id": self.adapter_id,
            "version": self.version,
            "pair_id": self.pair_id,
            "direction": self.direction,
            "speaker_digest": self.speaker_digest,
            "scope_digest": self.scope_digest,
            "base_model_id": self.base_model_id,
            "base_model_digest": self.base_model_digest,
            "backend": self.backend,
            "backend_digest": self.backend_digest,
            "dataset_digest": self.dataset_digest,
            "split_digest": self.split_digest,
            "evaluation_report_digest": self.evaluation_report_digest,
            "evaluation_policy_version": self.evaluation_policy_version,
            "consent_digest": self.consent_digest,
            "consent_expires_at_ms": self.consent_expires_at_ms,
            "artifact_ref": self.artifact_ref,
            "artifact_sha256": self.artifact_sha256,
            "artifact_size_bytes": self.artifact_size_bytes,
            "expires_at_ms": self.expires_at_ms,
            "status": self.status,
            "registry_version": self.registry_version,
            "approval_reason_code": self.approval_reason_code,
            "approved_at_ms": self.approved_at_ms,
            "revoked_at_ms": self.revoked_at_ms,
            "deprecated_at_ms": self.deprecated_at_ms,
            "expired_at_ms": self.expired_at_ms,
            "rollback_of_adapter_id": self.rollback_of_adapter_id,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "lineage": [dict(event) for event in self.lineage],
        }


@dataclass(frozen=True)
class SpeechAdapterExportReceipt:
    export_id: str
    encrypted_artifact_ref: str
    ciphertext_sha256: str
    size_bytes: int
    encryption_scheme: str = "AES-256-GCM"


class SpeechAdapterExportPort(Protocol):
    def verify_export_consent(
        self,
        record: "SpeechAdapterRecord",
        *,
        export_consent_digest: str,
        export_consent_epoch: int,
    ) -> bool: ...

    def encrypt_export(
        self,
        record: "SpeechAdapterRecord",
        *,
        destination_ref: str,
        export_consent_digest: str,
        export_consent_epoch: int,
    ) -> SpeechAdapterExportReceipt: ...


class SpeechAdapterExportLineagePort(Protocol):
    def publish_registration(self, record: "SpeechAdapterRecord") -> None: ...

    def publish_export(
        self,
        record: "SpeechAdapterRecord",
        receipt: SpeechAdapterExportReceipt,
        *,
        export_consent_digest: str,
    ) -> None: ...


__all__ = [
    "SpeechAdapterExportLineagePort",
    "SpeechAdapterExportPort",
    "SpeechAdapterExportReceipt",
    "SpeechAdapterNotFound",
    "SpeechAdapterRecord",
    "SpeechAdapterRegistryError",
    "SpeechAdapterVersionConflict",
]
