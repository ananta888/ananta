"""AES-GCM export adapter for explicitly consented speech adapters."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
from agent.repositories.speech_adaptation import SqlSpeechAdaptationArtifactRepository
from agent.repositories.speech_evidence_lineage import SpeechLineageEdge, SpeechLineageNode
from agent.services.ml_intern_speech_adapter_registry import (
    SpeechAdapterExportReceipt,
    SpeechAdapterRecord,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from ananta_contracts.speech_evidence_governance import (
    SpeechEvidenceConsent,
    SpeechEvidenceGovernanceError,
    canonical_json,
)


@dataclass(frozen=True, slots=True)
class SpeechAdapterExportConsentBinding:
    """Verified, content-free authority fence carried through one export."""

    consent_id: str
    consent_digest: str
    scope_digest: str
    session_epoch: int
    consent_version: int
    revocation_epoch: int
    expires_at_ms: int


class SpeechArtifactBlobPort(Protocol):
    def read(self, record: SpeechAdapterRecord, *, maximum_bytes: int) -> bytes: ...

    def write(
        self,
        record: SpeechAdapterRecord,
        artifact_ref: str,
        payload: bytes,
        *,
        media_type: str,
        export_consent: SpeechAdapterExportConsentBinding,
    ) -> tuple[str, int]: ...


class SpeechExportKeyPort(Protocol):
    def key(self, *, tenant_id: str, owner_subject: str, pair_id: str) -> bytes: ...


class SpeechExportConsentPort(Protocol):
    def verify(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
        adapter_id: str,
        speaker_digest: str,
        export_consent_digest: str,
        export_consent_epoch: int,
    ) -> SpeechAdapterExportConsentBinding | None: ...


class SpeechAdapterExportError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class SpeechAdapterExportConfigurationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class SqlSpeechAdapterExportConsentPort:
    """Verify one persistent, adapter-specific export grant.

    Export consent is deliberately distinct from training consent.  The
    existing governed consent table remains the sole authority; an export
    grant binds the target adapter through ``session_id`` and uses the
    dedicated ``speech_adapter_export`` purpose.
    """

    def __init__(self, *, clock_ms=None) -> None:
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def verify(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
        adapter_id: str,
        speaker_digest: str,
        export_consent_digest: str,
        export_consent_epoch: int,
    ) -> SpeechAdapterExportConsentBinding | None:
        now = int(self._clock_ms())
        with Session(engine) as session:
            row = session.exec(
                select(SpeechEvidenceConsentDB).where(
                    SpeechEvidenceConsentDB.tenant_id == tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == owner_subject,
                    SpeechEvidenceConsentDB.pair_id == pair_id,
                    SpeechEvidenceConsentDB.direction == direction,
                    SpeechEvidenceConsentDB.session_id == adapter_id,
                    SpeechEvidenceConsentDB.speaker_id == speaker_digest,
                    SpeechEvidenceConsentDB.purpose == "speech_adapter_export",
                    SpeechEvidenceConsentDB.consent_digest == export_consent_digest,
                    SpeechEvidenceConsentDB.state == "active",
                    SpeechEvidenceConsentDB.expires_at_ms > now,
                )
            ).first()
        if row is None:
            return None
        scope = dict(row.scope_payload or {})
        try:
            consent = SpeechEvidenceConsent.from_mapping(
                {
                    "schema": "ananta.speech-evidence-consent.v1",
                    "consent_id": row.id,
                    **scope,
                    "consent_version": row.consent_version,
                    "revocation_epoch": row.revocation_epoch,
                    "issued_at_ms": row.issued_at_ms,
                    "expires_at_ms": row.expires_at_ms,
                    "state": row.state,
                    "required_signers": list(row.required_signers or []),
                    "signatures": dict(row.signature_digests or {}),
                },
                now_ms=now,
            )
        except SpeechEvidenceGovernanceError:
            return None
        data_classes = set(consent.data_classes)
        if (
            consent.session_epoch != export_consent_epoch
            or consent.session_id != adapter_id
            or consent.tenant_id != tenant_id
            or consent.owner_subject != owner_subject
            or consent.pair_id != pair_id
            or consent.direction != direction
            or consent.speaker_id != speaker_digest
            or consent.purpose != "speech_adapter_export"
            or consent.grants.get("export") is not True
            or not data_classes
            or not data_classes <= {"acoustic_features", "speaker_embedding"}
            or not secrets.compare_digest(consent.scope_digest, row.scope_digest)
            or not secrets.compare_digest(consent.consent_digest, row.consent_digest)
            or not secrets.compare_digest(consent.consent_digest, export_consent_digest)
        ):
            return None
        return SpeechAdapterExportConsentBinding(
            consent_id=consent.consent_id,
            consent_digest=consent.consent_digest,
            scope_digest=consent.scope_digest,
            session_epoch=consent.session_epoch,
            consent_version=consent.consent_version,
            revocation_epoch=consent.revocation_epoch,
            expires_at_ms=consent.expires_at_ms,
        )


class ConfiguredPairExportKeyPort:
    """Derive a purpose-bound pair key from an operator-managed secret file."""

    def __init__(self, master_key: bytes) -> None:
        if not isinstance(master_key, bytes) or len(master_key) != 32:
            raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_invalid")
        self._master_key = bytes(master_key)

    @classmethod
    def from_file(cls, path_value: str) -> "ConfiguredPairExportKeyPort":
        candidate = Path(str(path_value or "").strip())
        if not candidate.is_absolute():
            raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_path_invalid")
        try:
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_path_invalid")
            if metadata.st_mode & 0o077:
                raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_permissions_invalid")
            raw = candidate.read_bytes()
        except SpeechAdapterExportConfigurationError:
            raise
        except OSError as exc:
            raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_unavailable") from exc
        if len(raw) > 256:
            raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_invalid")
        return cls(_decode_key(raw))

    def key(self, *, tenant_id: str, owner_subject: str, pair_id: str) -> bytes:
        scope = "\0".join((tenant_id, owner_subject, pair_id))
        if any(not 1 <= len(value) <= 192 for value in (tenant_id, owner_subject, pair_id)):
            raise SpeechAdapterExportError(
                "speech_adapter_export_key_scope_invalid",
                "speech adapter export key scope is invalid",
            )
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=hashlib.sha256(b"ananta-speech-adapter-export-salt-v1").digest(),
            info=f"ananta-speech-adapter-export-key-v1\0{scope}".encode("utf-8"),
        ).derive(self._master_key)


class SqlSpeechAdapterExportArtifactPort:
    """Bridge exports to the canonical speech-adaptation SQL/CAS store."""

    def __init__(
        self,
        repository: SqlSpeechAdaptationArtifactRepository,
        *,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        if audit is None:
            raise SpeechAdapterExportConfigurationError("speech_adapter_export_audit_unavailable")
        self._repository = repository
        self._audit = audit

    def read(self, record: SpeechAdapterRecord, *, maximum_bytes: int) -> bytes:
        return self._repository.read_committed_adapter(
            adapter_id=record.adapter_id,
            tenant_id=record.tenant_id,
            owner_subject=record.owner_subject,
            artifact_ref=record.artifact_ref,
            sha256=record.artifact_sha256,
            size_bytes=record.artifact_size_bytes,
            maximum_bytes=maximum_bytes,
        )

    def write(
        self,
        record: SpeechAdapterRecord,
        artifact_ref: str,
        payload: bytes,
        *,
        media_type: str,
        export_consent: SpeechAdapterExportConsentBinding,
    ) -> tuple[str, int]:
        ciphertext_digest = hashlib.sha256(payload).hexdigest()
        export_id = (
            "speech-export-"
            + hashlib.sha256((artifact_ref + ciphertext_digest).encode()).hexdigest()[:32]
        )
        receipt_digest = hashlib.sha256(
            canonical_json(
                {
                    "export_id": export_id,
                    "encrypted_artifact_ref": artifact_ref,
                    "ciphertext_sha256": ciphertext_digest,
                    "size_bytes": len(payload),
                    "encryption_scheme": "AES-256-GCM",
                    "export_consent_digest": export_consent.consent_digest,
                }
            )
        ).hexdigest()
        audit_event = self._audit.prepare_transition(
            idempotency_key=(
                f"speech-adapter-export:{export_id}:{export_consent.session_epoch}:"
                f"{export_consent.consent_version}:{export_consent.revocation_epoch}"
            ),
            tenant_id=record.tenant_id,
            scope=f"semantic-media-session:{record.pair_id}",
            event_type="speech_adapter",
            transition="exported",
            reason_code="speech_adapter_export_consent_verified",
            epoch=export_consent.session_epoch,
            contract_ref=export_consent.consent_digest,
            job_ref=record.adapter_id,
        )
        return self._repository.publish_encrypted_export(
            source_adapter_id=record.adapter_id,
            tenant_id=record.tenant_id,
            owner_subject=record.owner_subject,
            source_artifact_ref=record.artifact_ref,
            source_sha256=record.artifact_sha256,
            source_registry_version=record.registry_version,
            pair_id=record.pair_id,
            direction=record.direction,
            speaker_digest=record.speaker_digest,
            export_consent_id=export_consent.consent_id,
            export_consent_digest=export_consent.consent_digest,
            export_consent_scope_digest=export_consent.scope_digest,
            export_consent_session_epoch=export_consent.session_epoch,
            export_consent_version=export_consent.consent_version,
            export_consent_revocation_epoch=export_consent.revocation_epoch,
            destination_ref=artifact_ref,
            payload=payload,
            media_type=media_type,
            lineage_nodes=(
                SpeechLineageNode("adapter", record.artifact_sha256),
                SpeechLineageNode("export", ciphertext_digest),
                SpeechLineageNode("receipt", receipt_digest),
            ),
            lineage_edges=(
                SpeechLineageEdge(
                    "adapter", record.artifact_sha256,
                    "export", ciphertext_digest,
                    "exported_as",
                ),
                SpeechLineageEdge(
                    "export", ciphertext_digest,
                    "receipt", receipt_digest,
                    "acknowledged_by",
                ),
            ),
            audit_event=audit_event,
        )


@dataclass(frozen=True)
class EncryptedSpeechAdapterExportService:
    artifacts: SpeechArtifactBlobPort
    keys: SpeechExportKeyPort
    consents: SpeechExportConsentPort

    def verify_export_consent(
        self,
        record: SpeechAdapterRecord,
        *,
        export_consent_digest: str,
        export_consent_epoch: int,
    ) -> bool:
        return self.consents.verify(
            tenant_id=record.tenant_id,
            owner_subject=record.owner_subject,
            pair_id=record.pair_id,
            direction=record.direction,
            adapter_id=record.adapter_id,
            speaker_digest=record.speaker_digest,
            export_consent_digest=export_consent_digest,
            export_consent_epoch=export_consent_epoch,
        ) is not None

    def encrypt_export(
        self,
        record: SpeechAdapterRecord,
        *,
        destination_ref: str,
        export_consent_digest: str,
        export_consent_epoch: int,
    ) -> SpeechAdapterExportReceipt:
        export_consent = self.consents.verify(
            tenant_id=record.tenant_id,
            owner_subject=record.owner_subject,
            pair_id=record.pair_id,
            direction=record.direction,
            adapter_id=record.adapter_id,
            speaker_digest=record.speaker_digest,
            export_consent_digest=export_consent_digest,
            export_consent_epoch=export_consent_epoch,
        )
        if export_consent is None:
            raise SpeechAdapterExportError(
                "speech_adapter_export_consent_missing",
                "separate current speech adapter export consent is required",
            )
        plaintext = self.artifacts.read(record, maximum_bytes=record.artifact_size_bytes)
        if (
            len(plaintext) != record.artifact_size_bytes
            or hashlib.sha256(plaintext).hexdigest() != record.artifact_sha256
        ):
            raise SpeechAdapterExportError(
                "speech_adapter_export_source_mismatch",
                "speech adapter artifact changed before export",
            )
        key = self.keys.key(
            tenant_id=record.tenant_id,
            owner_subject=record.owner_subject,
            pair_id=record.pair_id,
        )
        if not isinstance(key, bytes) or len(key) != 32:
            raise SpeechAdapterExportError(
                "speech_adapter_export_key_invalid",
                "speech adapter export key must be 256-bit",
            )
        nonce = os.urandom(12)
        associated_data = json.dumps(
            {
                "adapter_id": record.adapter_id,
                "artifact_sha256": record.artifact_sha256,
                "consent_digest": export_consent.consent_digest,
                "consent_epoch": export_consent.session_epoch,
                "consent_version": export_consent.consent_version,
                "revocation_epoch": export_consent.revocation_epoch,
                "destination_ref": destination_ref,
                "direction": record.direction,
                "scope_digest": record.scope_digest,
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data)
        envelope = json.dumps(
            {
                "schema": "ananta.speech-adapter-export.v1",
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "associated_data": base64.b64encode(associated_data).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        stored_digest, stored_size = self.artifacts.write(
            record,
            destination_ref,
            envelope,
            media_type="application/vnd.ananta.speech-adapter-export+json",
            export_consent=export_consent,
        )
        expected_digest = hashlib.sha256(envelope).hexdigest()
        if stored_digest != expected_digest or stored_size != len(envelope):
            raise SpeechAdapterExportError(
                "speech_adapter_export_receipt_mismatch",
                "encrypted export storage receipt changed",
            )
        export_id = f"speech-export-{hashlib.sha256((destination_ref + expected_digest).encode()).hexdigest()[:32]}"
        return SpeechAdapterExportReceipt(
            export_id=export_id,
            encrypted_artifact_ref=destination_ref,
            ciphertext_sha256=expected_digest,
            size_bytes=len(envelope),
        )


def build_speech_adapter_export_service(
    source: Mapping[str, str] | None = None,
    *,
    audit: SemanticMediaAuditPort | None = None,
) -> EncryptedSpeechAdapterExportService:
    """Build the production export port or fail closed on incomplete config."""

    values = source or os.environ
    if audit is None:
        raise SpeechAdapterExportConfigurationError("speech_adapter_export_audit_unavailable")
    artifact_root = Path(
        str(values.get("ANANTA_SPEECH_TRAINING_ARTIFACT_ROOT") or "data/speech-adaptation/artifacts")
    )
    key_file = str(values.get("ANANTA_SPEECH_ADAPTER_EXPORT_KEY_FILE") or "").strip()
    if not key_file:
        raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_missing")
    repository = SqlSpeechAdaptationArtifactRepository(artifact_root)
    return EncryptedSpeechAdapterExportService(
        artifacts=SqlSpeechAdapterExportArtifactPort(repository, audit=audit),
        keys=ConfiguredPairExportKeyPort.from_file(key_file),
        consents=SqlSpeechAdapterExportConsentPort(),
    )


def _decode_key(raw: bytes) -> bytes:
    value = raw.strip()
    if len(value) == 32:
        return value
    try:
        text = value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_invalid") from exc
    if len(text) == 64:
        try:
            decoded = bytes.fromhex(text)
        except ValueError:
            decoded = b""
        if len(decoded) == 32:
            return decoded
    try:
        decoded = base64.b64decode(text, validate=True)
    except ValueError as exc:
        raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_invalid") from exc
    if len(decoded) != 32:
        raise SpeechAdapterExportConfigurationError("speech_adapter_export_key_invalid")
    return decoded


__all__ = [
    "ConfiguredPairExportKeyPort",
    "EncryptedSpeechAdapterExportService",
    "SpeechAdapterExportConfigurationError",
    "SpeechAdapterExportError",
    "SqlSpeechAdapterExportArtifactPort",
    "SqlSpeechAdapterExportConsentPort",
    "build_speech_adapter_export_service",
]
