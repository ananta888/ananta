"""Hub artifact SQL/CAS repository for speech-adaptation worker outputs."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_adaptation import SpeechAdaptationArtifactDB, SpeechAdaptationJobDB
from agent.models.semantic_media_audit import SemanticMediaAuditEvent
from agent.models.speech_adaptation_admission import SpeechAdaptationDecisionConflict, SpeechPrincipal
from agent.repositories.speech_adaptation_export_publisher import SpeechAdapterExportPublisher
from agent.repositories.speech_adaptation_storage import SPEECH_ADAPTATION_WRITE_LOCK as _WRITE_LOCK
from agent.repositories.speech_adaptation_storage import file_sha256 as _file_sha256
from ananta_contracts.speech_adaptation import SpeechAdaptationResult


class SqlSpeechAdaptationArtifactRepository:
    """Verify and atomically persist bytes supplied by the isolated worker."""

    def __init__(
        self,
        root: Path,
        *,
        export_publisher: SpeechAdapterExportPublisher | None = None,
    ) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root.chmod(0o700)
        self._export_publisher = (
            export_publisher if export_publisher is not None else SpeechAdapterExportPublisher(root=self._root)
        )

    def read_committed_adapter(
        self,
        *,
        adapter_id: str,
        tenant_id: str,
        owner_subject: str,
        artifact_ref: str,
        sha256: str,
        size_bytes: int,
        maximum_bytes: int,
    ) -> bytes:
        """Read one registry-bound adapter from the canonical Hub store.

        Export callers cannot turn an opaque artifact reference into an
        arbitrary filesystem read.  Every public binding is re-checked
        against the committed SQL receipt before the internal CAS path is
        resolved.
        """

        if type(maximum_bytes) is not int or not 1 <= size_bytes <= maximum_bytes <= 8 * 1024**3:
            raise SpeechAdaptationDecisionConflict("speech_export_source_size_invalid")
        with Session(engine) as session:
            row = session.exec(
                select(SpeechAdaptationArtifactDB).where(
                    SpeechAdaptationArtifactDB.id == adapter_id,
                    SpeechAdaptationArtifactDB.tenant_id == tenant_id,
                    SpeechAdaptationArtifactDB.owner_subject == owner_subject,
                    SpeechAdaptationArtifactDB.artifact_ref == artifact_ref,
                    SpeechAdaptationArtifactDB.sha256 == sha256,
                    SpeechAdaptationArtifactDB.size_bytes == size_bytes,
                    SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-adapter",
                    SpeechAdaptationArtifactDB.state == "committed",
                )
            ).first()
            if row is None:
                raise SpeechAdaptationDecisionConflict("speech_export_source_not_committed")
            job_id = row.job_id
            attempt_id = row.attempt_id
            storage_ref = row.storage_ref
        expected_storage_ref = f"hub-artifact://speech-adaptation/{job_id}/{attempt_id}/{sha256}"
        if not secrets.compare_digest(storage_ref, expected_storage_ref):
            raise SpeechAdaptationDecisionConflict("speech_export_source_storage_mismatch")
        source = (self._root / job_id / attempt_id / sha256).resolve()
        try:
            source.relative_to(self._root)
        except ValueError as exc:
            raise SpeechAdaptationDecisionConflict("speech_artifact_storage_boundary") from exc
        try:
            with source.open("rb") as handle:
                payload = handle.read(maximum_bytes + 1)
        except OSError as exc:
            raise SpeechAdaptationDecisionConflict("speech_export_source_unavailable") from exc
        if (
            len(payload) != size_bytes
            or len(payload) > maximum_bytes
            or not secrets.compare_digest(hashlib.sha256(payload).hexdigest(), sha256)
        ):
            raise SpeechAdaptationDecisionConflict("speech_export_source_mismatch")
        return payload

    def publish_encrypted_export(
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

        return self._export_publisher.publish(
            source_adapter_id=source_adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            source_artifact_ref=source_artifact_ref,
            source_sha256=source_sha256,
            source_registry_version=source_registry_version,
            pair_id=pair_id,
            direction=direction,
            speaker_digest=speaker_digest,
            export_consent_id=export_consent_id,
            export_consent_digest=export_consent_digest,
            export_consent_scope_digest=export_consent_scope_digest,
            export_consent_session_epoch=export_consent_session_epoch,
            export_consent_version=export_consent_version,
            export_consent_revocation_epoch=export_consent_revocation_epoch,
            destination_ref=destination_ref,
            payload=payload,
            media_type=media_type,
            lineage_nodes=lineage_nodes,
            lineage_edges=lineage_edges,
            audit_event=audit_event,
        )

    def publish(
        self,
        *,
        job: SpeechAdaptationJobDB,
        artifact_id: str,
        attempt_id: str,
        artifact_ref: str,
        sha256: str,
        size_bytes: int,
        media_type: str,
        stream: BinaryIO,
    ) -> SpeechAdaptationArtifactDB:
        contract = dict(job.contract_payload or {})
        if attempt_id != str(contract.get("attempt", {}).get("attempt_id") or ""):
            raise SpeechAdaptationDecisionConflict("speech_artifact_attempt_mismatch")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise SpeechAdaptationDecisionConflict("speech_artifact_digest_invalid")
        budget = dict(contract.get("budget") or {})
        target = dict(contract.get("artifact_target") or {})
        checkpoint_prefix = f"artifact://speech-checkpoints/{job.id}/{attempt_id}/"
        if media_type == "application/vnd.ananta.speech-adapter":
            if (artifact_id, artifact_ref) != (
                str(target.get("target_id") or ""),
                str(target.get("artifact_ref") or ""),
            ):
                raise SpeechAdaptationDecisionConflict("speech_artifact_target_mismatch")
            maximum_size = int(budget.get("max_artifact_bytes") or 0)
        elif media_type == "application/vnd.ananta.speech-checkpoint":
            if artifact_id != f"speech-checkpoint-{sha256[:32]}" or artifact_ref != f"{checkpoint_prefix}{sha256}":
                raise SpeechAdaptationDecisionConflict("speech_checkpoint_target_mismatch")
            maximum_size = int(budget.get("max_disk_bytes") or 0)
        elif media_type == "application/vnd.ananta.speech-evaluation+json":
            expected_ref = f"artifact://speech-evaluations/{job.id}/{attempt_id}/{sha256}"
            if artifact_id != f"speech-evaluation-{sha256[:32]}" or artifact_ref != expected_ref:
                raise SpeechAdaptationDecisionConflict("speech_evaluation_target_mismatch")
            maximum_size = min(
                int(budget.get("max_artifact_bytes") or 0),
                8 * 1024**2,
            )
        else:
            raise SpeechAdaptationDecisionConflict("speech_artifact_media_type_invalid")
        if type(size_bytes) is not int or not 1 <= size_bytes <= maximum_size:
            raise SpeechAdaptationDecisionConflict("speech_artifact_size_invalid")
        with _WRITE_LOCK:
            with Session(engine) as session:
                existing_media = session.exec(
                    select(SpeechAdaptationArtifactDB).where(
                        SpeechAdaptationArtifactDB.job_id == job.id,
                        SpeechAdaptationArtifactDB.attempt_id == attempt_id,
                        SpeechAdaptationArtifactDB.media_type == media_type,
                    )
                ).first()
                if existing_media is not None and (
                    existing_media.id,
                    existing_media.artifact_ref,
                    existing_media.sha256,
                    existing_media.size_bytes,
                ) != (artifact_id, artifact_ref, sha256, size_bytes):
                    raise SpeechAdaptationDecisionConflict("speech_artifact_receipt_conflict")
        destination = (self._root / job.id / attempt_id / sha256).resolve()
        try:
            destination.relative_to(self._root)
        except ValueError as exc:
            raise SpeechAdaptationDecisionConflict("speech_artifact_storage_boundary") from exc
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.parent.chmod(0o700)
        temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
        digest = hashlib.sha256()
        written = 0
        try:
            with temporary.open("xb") as handle:
                temporary.chmod(0o600)
                while True:
                    chunk = stream.read(min(1024 * 1024, size_bytes - written + 1))
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > size_bytes:
                        raise SpeechAdaptationDecisionConflict("speech_artifact_size_mismatch")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if written != size_bytes or not secrets.compare_digest(digest.hexdigest(), sha256):
                raise SpeechAdaptationDecisionConflict("speech_artifact_digest_mismatch")
            if destination.exists():
                if destination.stat().st_size != size_bytes or _file_sha256(destination) != sha256:
                    raise SpeechAdaptationDecisionConflict("speech_artifact_storage_conflict")
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, destination)
            row = SpeechAdaptationArtifactDB(
                id=artifact_id,
                tenant_id=job.tenant_id,
                owner_subject=job.owner_subject,
                job_id=job.id,
                attempt_id=attempt_id,
                artifact_ref=artifact_ref,
                sha256=sha256,
                size_bytes=size_bytes,
                media_type=media_type,
                storage_ref=f"hub-artifact://speech-adaptation/{job.id}/{attempt_id}/{sha256}",
            )
            with _WRITE_LOCK:
                with Session(engine) as session:
                    existing = session.exec(
                        select(SpeechAdaptationArtifactDB).where(
                            SpeechAdaptationArtifactDB.job_id == job.id,
                            SpeechAdaptationArtifactDB.attempt_id == attempt_id,
                            SpeechAdaptationArtifactDB.artifact_ref == artifact_ref,
                        )
                    ).first()
                    if existing is not None:
                        if (
                            existing.id,
                            existing.sha256,
                            existing.size_bytes,
                            existing.media_type,
                        ) != (artifact_id, sha256, size_bytes, media_type):
                            raise SpeechAdaptationDecisionConflict("speech_artifact_receipt_conflict")
                        session.expunge(existing)
                        return existing
                    session.add(row)
                    try:
                        session.commit()
                    except IntegrityError as exc:
                        session.rollback()
                        conflict = session.exec(
                            select(SpeechAdaptationArtifactDB).where(
                                SpeechAdaptationArtifactDB.job_id == job.id,
                                SpeechAdaptationArtifactDB.attempt_id == attempt_id,
                                SpeechAdaptationArtifactDB.media_type == media_type,
                            )
                        ).first()
                        if conflict is not None and (
                            conflict.id,
                            conflict.artifact_ref,
                            conflict.sha256,
                            conflict.size_bytes,
                        ) == (artifact_id, artifact_ref, sha256, size_bytes):
                            session.expunge(conflict)
                            return conflict
                        raise SpeechAdaptationDecisionConflict("speech_artifact_receipt_conflict") from exc
                    session.refresh(row)
                    session.expunge(row)
                    return row
        finally:
            temporary.unlink(missing_ok=True)

    def verify_and_commit(
        self,
        principal: SpeechPrincipal,
        job,
        result: SpeechAdaptationResult,
    ) -> None:
        """CAS-bind a terminal result to bytes already accepted by the Hub."""

        if result.status == "completed":
            if result.artifact is None:
                raise SpeechAdaptationDecisionConflict("speech_result_artifact_missing")
            with _WRITE_LOCK:
                with Session(engine) as session:
                    artifact = session.exec(
                        select(SpeechAdaptationArtifactDB).where(
                            SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                            SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                            SpeechAdaptationArtifactDB.job_id == job.job_id,
                            SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                            SpeechAdaptationArtifactDB.id == result.artifact.artifact_id,
                            SpeechAdaptationArtifactDB.artifact_ref == result.artifact.artifact_ref,
                            SpeechAdaptationArtifactDB.sha256 == result.artifact.sha256,
                            SpeechAdaptationArtifactDB.size_bytes == result.artifact.size_bytes,
                            SpeechAdaptationArtifactDB.media_type == result.artifact.media_type,
                            SpeechAdaptationArtifactDB.state.in_({"pending", "committed"}),
                        )
                    ).first()
                    if artifact is None:
                        raise SpeechAdaptationDecisionConflict("speech_result_artifact_not_published_by_hub")
                    if result.checkpoint_digest is not None:
                        checkpoint = session.exec(
                            select(SpeechAdaptationArtifactDB).where(
                                SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                                SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                                SpeechAdaptationArtifactDB.job_id == job.job_id,
                                SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                                SpeechAdaptationArtifactDB.sha256 == result.checkpoint_digest,
                                SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-checkpoint",
                                SpeechAdaptationArtifactDB.state.in_({"pending", "checkpointed"}),
                            )
                        ).first()
                        if checkpoint is None:
                            raise SpeechAdaptationDecisionConflict("speech_result_checkpoint_not_published_by_hub")
                        if checkpoint.state == "pending":
                            checkpoint.state = "checkpointed"
                            checkpoint.updated_at_ms = time.time_ns() // 1_000_000
                            session.add(checkpoint)
                    evaluation = session.exec(
                        select(SpeechAdaptationArtifactDB).where(
                            SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                            SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                            SpeechAdaptationArtifactDB.job_id == job.job_id,
                            SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                            SpeechAdaptationArtifactDB.sha256 == result.evaluation_report_digest,
                            SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-evaluation+json",
                            SpeechAdaptationArtifactDB.state.in_({"pending", "evaluated"}),
                        )
                    ).first()
                    if evaluation is None:
                        raise SpeechAdaptationDecisionConflict("speech_result_evaluation_not_published_by_hub")
                    if evaluation.state == "pending":
                        evaluation.state = "evaluated"
                        evaluation.updated_at_ms = time.time_ns() // 1_000_000
                        session.add(evaluation)
                    if artifact.state == "pending":
                        artifact.state = "committed"
                        artifact.updated_at_ms = time.time_ns() // 1_000_000
                        session.add(artifact)
                    session.commit()
            return
        self.reject_attempt(principal, job)

    def read_evaluation(
        self,
        principal: SpeechPrincipal,
        job,
        evaluation_digest: str,
    ) -> dict:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechAdaptationArtifactDB).where(
                    SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                    SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                    SpeechAdaptationArtifactDB.job_id == job.job_id,
                    SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                    SpeechAdaptationArtifactDB.sha256 == evaluation_digest,
                    SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-evaluation+json",
                    SpeechAdaptationArtifactDB.state == "evaluated",
                )
            ).first()
            if row is None:
                raise SpeechAdaptationDecisionConflict("speech_evaluation_report_not_available")
            size_bytes = row.size_bytes
        path = (self._root / job.job_id / job.attempt.attempt_id / evaluation_digest).resolve()
        try:
            path.relative_to(self._root)
            content = path.read_bytes()
        except (ValueError, OSError) as exc:
            raise SpeechAdaptationDecisionConflict("speech_evaluation_report_storage_invalid") from exc
        if (
            len(content) != size_bytes
            or len(content) > min(job.budget.max_artifact_bytes, 8 * 1024**2)
            or hashlib.sha256(content).hexdigest() != evaluation_digest
        ):
            raise SpeechAdaptationDecisionConflict("speech_evaluation_report_storage_invalid")
        try:
            payload = json.loads(content.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeError, ValueError) as exc:
            raise SpeechAdaptationDecisionConflict("speech_evaluation_report_storage_invalid") from exc
        if not isinstance(payload, dict):
            raise SpeechAdaptationDecisionConflict("speech_evaluation_report_storage_invalid")
        return payload

    def reject_attempt(self, principal: SpeechPrincipal, job) -> None:
        with _WRITE_LOCK:
            with Session(engine) as session:
                rows = session.exec(
                    select(SpeechAdaptationArtifactDB).where(
                        SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                        SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                        SpeechAdaptationArtifactDB.job_id == job.job_id,
                        SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                        SpeechAdaptationArtifactDB.state == "pending",
                    )
                ).all()
                for row in rows:
                    destination = (self._root / row.job_id / row.attempt_id / row.sha256).resolve()
                    try:
                        destination.relative_to(self._root)
                    except ValueError as exc:
                        raise SpeechAdaptationDecisionConflict("speech_artifact_storage_boundary") from exc
                    try:
                        destination.unlink(missing_ok=True)
                    except OSError as exc:
                        raise SpeechAdaptationDecisionConflict("speech_artifact_rejection_cleanup_failed") from exc
                    row.state = "rejected"
                    row.updated_at_ms = time.time_ns() // 1_000_000
                    session.add(row)
                session.commit()


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON is forbidden")


__all__ = ["SqlSpeechAdaptationArtifactRepository"]
