"""Encrypted speech-adapter export publication into the Hub artifact SQL/CAS store.

The publisher re-fences the committed source adapter, the approved registry
entry and the active export consent inside the same SQL transaction that
records the export receipt, its lineage and its content-free audit event.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.ml_intern_training import MlInternSpeechAdapterDB
from agent.db_models.speech_adaptation import SpeechAdaptationArtifactDB
from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
from agent.models.semantic_media_audit import SemanticMediaAuditEvent
from agent.models.speech_adaptation_admission import SpeechAdaptationDecisionConflict
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_adaptation_storage import SPEECH_ADAPTATION_WRITE_LOCK as _WRITE_LOCK
from agent.repositories.speech_adaptation_storage import file_sha256 as _file_sha256


class SpeechAdapterExportPublisher:
    """Persist one encrypted adapter export below an already-resolved artifact root."""

    def __init__(self, *, root: Path) -> None:
        self._root = root

    def publish(
        self,
        *,
        source_adapter_id: str,
        tenant_id: str,
        owner_subject: str,
        source_artifact_ref: str,
        source_sha256: str,
        source_registry_version: int,
        pair_id: str,
        direction: str,
        speaker_digest: str,
        export_consent_id: str,
        export_consent_digest: str,
        export_consent_scope_digest: str,
        export_consent_session_epoch: int,
        export_consent_version: int,
        export_consent_revocation_epoch: int,
        destination_ref: str,
        payload: bytes,
        media_type: str,
        lineage_nodes=(),
        lineage_edges=(),
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> tuple[str, int]:
        """Persist an encrypted export in the existing artifact SQL/CAS SOT."""

        if (
            not destination_ref.startswith("artifact://speech-adapter-exports/")
            or ".." in destination_ref.split("/")
            or len(destination_ref) > 512
            or media_type != "application/vnd.ananta.speech-adapter-export+json"
            or not isinstance(payload, bytes)
            or not 1 <= len(payload) <= 8 * 1024**3
        ):
            raise SpeechAdaptationDecisionConflict("speech_export_target_invalid")
        digest = hashlib.sha256(payload).hexdigest()
        export_id = f"speech-export-{hashlib.sha256((destination_ref + digest).encode()).hexdigest()[:32]}"
        export_attempt_id = f"export-{hashlib.sha256(destination_ref.encode()).hexdigest()[:32]}"
        with _WRITE_LOCK, Session(engine) as session:
            source = session.exec(
                select(SpeechAdaptationArtifactDB).where(
                    SpeechAdaptationArtifactDB.id == source_adapter_id,
                    SpeechAdaptationArtifactDB.tenant_id == tenant_id,
                    SpeechAdaptationArtifactDB.owner_subject == owner_subject,
                    SpeechAdaptationArtifactDB.artifact_ref == source_artifact_ref,
                    SpeechAdaptationArtifactDB.sha256 == source_sha256,
                    SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-adapter",
                    SpeechAdaptationArtifactDB.state == "committed",
                )
            ).first()
            if source is None:
                raise SpeechAdaptationDecisionConflict("speech_export_source_not_committed")
            existing = session.exec(
                select(SpeechAdaptationArtifactDB).where(
                    SpeechAdaptationArtifactDB.tenant_id == tenant_id,
                    SpeechAdaptationArtifactDB.owner_subject == owner_subject,
                    SpeechAdaptationArtifactDB.artifact_ref == destination_ref,
                )
            ).first()
            if existing is not None and (
                existing.id,
                existing.job_id,
                existing.sha256,
                existing.size_bytes,
                existing.media_type,
                existing.state,
            ) != (
                export_id,
                source.job_id,
                digest,
                len(payload),
                media_type,
                "committed",
            ):
                raise SpeechAdaptationDecisionConflict("speech_export_receipt_conflict")
            job_id = source.job_id
        destination = (self._root / "exports" / digest).resolve()
        try:
            destination.relative_to(self._root)
        except ValueError as exc:
            raise SpeechAdaptationDecisionConflict("speech_artifact_storage_boundary") from exc
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.parent.chmod(0o700)
        temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
        created_destination = False
        storage_ref = f"hub-artifact://speech-adaptation/exports/{digest}"
        _WRITE_LOCK.acquire()
        try:
            if destination.exists():
                if destination.stat().st_size != len(payload) or _file_sha256(destination) != digest:
                    raise SpeechAdaptationDecisionConflict("speech_export_storage_conflict")
            else:
                with temporary.open("xb") as handle:
                    temporary.chmod(0o600)
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
                created_destination = True
            row = SpeechAdaptationArtifactDB(
                id=export_id,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                job_id=job_id,
                attempt_id=export_attempt_id,
                artifact_ref=destination_ref,
                sha256=digest,
                size_bytes=len(payload),
                media_type=media_type,
                storage_ref=storage_ref,
                state="committed",
            )
            with _WRITE_LOCK, Session(engine) as session:
                now_ms = time.time_ns() // 1_000_000
                source_fence = session.exec(
                    select(SpeechAdaptationArtifactDB)
                    .where(
                        SpeechAdaptationArtifactDB.id == source_adapter_id,
                        SpeechAdaptationArtifactDB.tenant_id == tenant_id,
                        SpeechAdaptationArtifactDB.owner_subject == owner_subject,
                        SpeechAdaptationArtifactDB.job_id == job_id,
                        SpeechAdaptationArtifactDB.artifact_ref == source_artifact_ref,
                        SpeechAdaptationArtifactDB.sha256 == source_sha256,
                        SpeechAdaptationArtifactDB.media_type
                        == "application/vnd.ananta.speech-adapter",
                        SpeechAdaptationArtifactDB.state == "committed",
                    )
                    .with_for_update()
                ).first()
                if source_fence is None:
                    raise SpeechAdaptationDecisionConflict("speech_export_source_fence_changed")
                adapter_fence = session.exec(
                    select(MlInternSpeechAdapterDB)
                    .where(
                        MlInternSpeechAdapterDB.id == source_adapter_id,
                        MlInternSpeechAdapterDB.tenant_id == tenant_id,
                        MlInternSpeechAdapterDB.owner_subject == owner_subject,
                        MlInternSpeechAdapterDB.pair_id == pair_id,
                        MlInternSpeechAdapterDB.direction == direction,
                        MlInternSpeechAdapterDB.speaker_digest == speaker_digest,
                        MlInternSpeechAdapterDB.registry_version == source_registry_version,
                        MlInternSpeechAdapterDB.status == "approved",
                        MlInternSpeechAdapterDB.artifact_ref == source_artifact_ref,
                        MlInternSpeechAdapterDB.artifact_sha256 == source_sha256,
                        MlInternSpeechAdapterDB.expires_at_ms > now_ms,
                        MlInternSpeechAdapterDB.consent_expires_at_ms > now_ms,
                    )
                    .with_for_update()
                ).first()
                if adapter_fence is None:
                    raise SpeechAdaptationDecisionConflict("speech_export_adapter_fence_changed")
                consent_fence = session.exec(
                    select(SpeechEvidenceConsentDB)
                    .where(
                        SpeechEvidenceConsentDB.id == export_consent_id,
                        SpeechEvidenceConsentDB.tenant_id == tenant_id,
                        SpeechEvidenceConsentDB.owner_subject == owner_subject,
                        SpeechEvidenceConsentDB.pair_id == pair_id,
                        SpeechEvidenceConsentDB.direction == direction,
                        SpeechEvidenceConsentDB.session_id == source_adapter_id,
                        SpeechEvidenceConsentDB.speaker_id == speaker_digest,
                        SpeechEvidenceConsentDB.purpose == "speech_adapter_export",
                        SpeechEvidenceConsentDB.scope_digest == export_consent_scope_digest,
                        SpeechEvidenceConsentDB.consent_digest == export_consent_digest,
                        SpeechEvidenceConsentDB.session_epoch == export_consent_session_epoch,
                        SpeechEvidenceConsentDB.consent_version == export_consent_version,
                        SpeechEvidenceConsentDB.revocation_epoch
                        == export_consent_revocation_epoch,
                        SpeechEvidenceConsentDB.state == "active",
                        SpeechEvidenceConsentDB.expires_at_ms > now_ms,
                    )
                    .with_for_update()
                ).first()
                if consent_fence is None:
                    raise SpeechAdaptationDecisionConflict("speech_export_consent_fence_changed")
                consent_scope = dict(consent_fence.scope_payload or {})
                consent_grants = dict(consent_scope.get("grants") or {})
                consent_classes = set(consent_scope.get("data_classes") or ())
                if (
                    consent_grants.get("export") is not True
                    or not consent_classes
                    or not consent_classes <= {"acoustic_features", "speaker_embedding"}
                    or consent_scope.get("session_id") != source_adapter_id
                    or consent_scope.get("session_epoch") != export_consent_session_epoch
                ):
                    raise SpeechAdaptationDecisionConflict("speech_export_consent_fence_changed")
                existing = session.get(SpeechAdaptationArtifactDB, export_id)
                if existing is None:
                    session.add(row)
                elif (
                    existing.tenant_id,
                    existing.owner_subject,
                    existing.job_id,
                    existing.artifact_ref,
                    existing.sha256,
                    existing.size_bytes,
                    existing.media_type,
                    existing.state,
                ) != (
                    tenant_id,
                    owner_subject,
                    job_id,
                    destination_ref,
                    digest,
                    len(payload),
                    media_type,
                    "committed",
                ):
                    raise SpeechAdaptationDecisionConflict("speech_export_receipt_conflict")
                if lineage_nodes:
                    from agent.repositories.speech_evidence_lineage import SpeechEvidenceLineageRepository

                    SpeechEvidenceLineageRepository().stage(
                        session,
                        tenant_id=tenant_id,
                        owner_subject=owner_subject,
                        nodes=tuple(lineage_nodes),
                        edges=tuple(lineage_edges),
                        now_ms=time.time_ns() // 1_000_000,
                    )
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                try:
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    raise SpeechAdaptationDecisionConflict("speech_export_receipt_conflict") from exc
            return digest, len(payload)
        except Exception:
            # The encrypted bytes are written before the SQL/outbox transaction
            # so a committed receipt can never point at a missing artifact.  If
            # that transaction fails, remove only the CAS object created by this
            # call and only while no committed receipt references it.
            if created_destination:
                with Session(engine) as session:
                    referenced = session.exec(
                        select(SpeechAdaptationArtifactDB.id).where(
                            SpeechAdaptationArtifactDB.storage_ref == storage_ref
                        )
                    ).first()
                if referenced is None:
                    destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            _WRITE_LOCK.release()


__all__ = ["SpeechAdapterExportPublisher"]
