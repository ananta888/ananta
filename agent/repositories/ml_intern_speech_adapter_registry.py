"""SQL authority for pair-scoped speech-adapter registry state."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import MlInternSpeechAdapterDB, MlInternSpeechAdapterLegacyImportDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    SpeechLineageEdge,
    SpeechLineageNode,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent

_WRITE_LOCK = threading.RLock()
_IMMUTABLE_FIELDS = (
    "version",
    "tenant_id",
    "owner_subject",
    "pair_id",
    "direction",
    "speaker_digest",
    "scope_digest",
    "base_model_id",
    "base_model_digest",
    "backend",
    "backend_digest",
    "dataset_digest",
    "split_digest",
    "evaluation_report_digest",
    "evaluation_policy_version",
    "consent_digest",
    "artifact_ref",
    "artifact_sha256",
)


class MlInternSpeechAdapterRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SpeechAdapterCasMutation:
    record: Mapping[str, Any]
    expected_version: int
    audit_event: SemanticMediaAuditEvent | None


class MlInternSpeechAdapterRepository:
    """Tenant-scoped adapter repository with CAS and atomic audit enqueue.

    JSON registries are accepted only through :meth:`import_legacy_once`.
    Normal reads and writes always use the Hub SQL database.
    """

    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def create(
        self,
        record: Mapping[str, Any],
        *,
        audit_event: SemanticMediaAuditEvent | None,
        lineage_nodes: Sequence[SpeechLineageNode] = (),
        lineage_edges: Sequence[SpeechLineageEdge] = (),
    ) -> tuple[dict[str, Any], bool]:
        adapter_id = str(record["adapter_id"])
        with _WRITE_LOCK, Session(self._engine) as db:
            existing = db.get(MlInternSpeechAdapterDB, adapter_id)
            if existing is not None:
                self._assert_replay(existing, record)
                self._enqueue(db, audit_event)
                self._stage_lineage(db, record, lineage_nodes, lineage_edges)
                db.commit()
                return self._record(existing), True
            row = self._row(record)
            db.add(row)
            try:
                with db.no_autoflush:
                    self._enqueue(db, audit_event)
                    self._stage_lineage(db, record, lineage_nodes, lineage_edges)
                db.commit()
            except IntegrityError:
                db.rollback()
                existing = db.get(MlInternSpeechAdapterDB, adapter_id)
                if existing is None:
                    raise
                self._assert_replay(existing, record)
                self._enqueue(db, audit_event)
                self._stage_lineage(db, record, lineage_nodes, lineage_edges)
                db.commit()
                return self._record(existing), True
            db.refresh(row)
            return self._record(row), False

    def get(
        self,
        adapter_id: str,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str | None = None,
        direction: str | None = None,
    ) -> dict[str, Any] | None:
        with Session(self._engine) as db:
            statement = select(MlInternSpeechAdapterDB).where(
                MlInternSpeechAdapterDB.id == adapter_id,
                MlInternSpeechAdapterDB.tenant_id == tenant_id,
                MlInternSpeechAdapterDB.owner_subject == owner_subject,
            )
            if pair_id is not None:
                statement = statement.where(MlInternSpeechAdapterDB.pair_id == pair_id)
            if direction is not None:
                statement = statement.where(MlInternSpeechAdapterDB.direction == direction)
            row = db.exec(statement).first()
            return None if row is None else self._record(row)

    def list_for_pair(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
    ) -> list[dict[str, Any]]:
        with Session(self._engine) as db:
            rows = db.exec(
                select(MlInternSpeechAdapterDB)
                .where(
                    MlInternSpeechAdapterDB.tenant_id == tenant_id,
                    MlInternSpeechAdapterDB.owner_subject == owner_subject,
                    MlInternSpeechAdapterDB.pair_id == pair_id,
                    MlInternSpeechAdapterDB.direction == direction,
                )
                .order_by(MlInternSpeechAdapterDB.created_at_ms, MlInternSpeechAdapterDB.id)
            ).all()
            return [self._record(row) for row in rows]

    def list_by_artifact(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        artifact_sha256: str,
    ) -> list[dict[str, Any]]:
        with Session(self._engine) as db:
            rows = db.exec(
                select(MlInternSpeechAdapterDB)
                .where(
                    MlInternSpeechAdapterDB.tenant_id == tenant_id,
                    MlInternSpeechAdapterDB.owner_subject == owner_subject,
                    MlInternSpeechAdapterDB.artifact_sha256 == artifact_sha256,
                )
                .order_by(MlInternSpeechAdapterDB.id)
            ).all()
            return [self._record(row) for row in rows]

    def replace(
        self,
        record: Mapping[str, Any],
        *,
        expected_version: int,
        audit_event: SemanticMediaAuditEvent | None,
    ) -> dict[str, Any]:
        mutation = SpeechAdapterCasMutation(record, expected_version, audit_event)
        return self.replace_many((mutation,))[0]

    def replace_many(self, mutations: Sequence[SpeechAdapterCasMutation]) -> list[dict[str, Any]]:
        if not mutations:
            return []
        ids = [str(mutation.record["adapter_id"]) for mutation in mutations]
        if len(ids) != len(set(ids)):
            raise MlInternSpeechAdapterRepositoryError("speech_adapter_duplicate_mutation")
        with _WRITE_LOCK, Session(self._engine) as db:
            rows: list[MlInternSpeechAdapterDB] = []
            for mutation in mutations:
                record = mutation.record
                row = db.exec(
                    select(MlInternSpeechAdapterDB)
                    .where(
                        MlInternSpeechAdapterDB.id == str(record["adapter_id"]),
                        MlInternSpeechAdapterDB.tenant_id == str(record["tenant_id"]),
                        MlInternSpeechAdapterDB.owner_subject == str(record["owner_subject"]),
                    )
                    .with_for_update()
                ).first()
                if row is None:
                    raise MlInternSpeechAdapterRepositoryError("speech_adapter_not_found")
                if row.registry_version != mutation.expected_version:
                    raise MlInternSpeechAdapterRepositoryError("speech_adapter_version_conflict")
                if int(record["registry_version"]) != mutation.expected_version + 1:
                    raise MlInternSpeechAdapterRepositoryError("speech_adapter_invalid_next_version")
                self._copy_record(row, record)
                db.add(row)
                rows.append(row)
            for mutation in mutations:
                self._enqueue(db, mutation.audit_event)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise MlInternSpeechAdapterRepositoryError("speech_adapter_version_conflict") from exc
            for row in rows:
                db.refresh(row)
            return [self._record(row) for row in rows]

    def enqueue_replay(self, events: Sequence[SemanticMediaAuditEvent | None]) -> None:
        """Repair a missing outbox command without changing authority state."""

        with _WRITE_LOCK, Session(self._engine) as db:
            for event in events:
                self._enqueue(db, event)
            db.commit()

    def import_legacy_once(
        self,
        *,
        source_digest: str,
        records: Sequence[Mapping[str, Any]],
        audit_events: Sequence[SemanticMediaAuditEvent | None],
        imported_at_ms: int,
    ) -> bool:
        """Import one immutable JSON snapshot once, fenced by its digest."""

        if len(records) != len(audit_events):
            raise ValueError("legacy adapter records and audit events must align")
        with _WRITE_LOCK, Session(self._engine) as db:
            if db.get(MlInternSpeechAdapterLegacyImportDB, source_digest) is not None:
                return False
            imported = 0
            for record, event in zip(records, audit_events, strict=True):
                adapter_id = str(record["adapter_id"])
                existing = db.get(MlInternSpeechAdapterDB, adapter_id)
                if existing is not None:
                    self._assert_replay(existing, record)
                    continue
                db.add(self._row(record))
                self._enqueue(db, event)
                imported += 1
            db.add(
                MlInternSpeechAdapterLegacyImportDB(
                    source_digest=source_digest,
                    record_count=imported,
                    imported_at_ms=imported_at_ms,
                )
            )
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                if db.get(MlInternSpeechAdapterLegacyImportDB, source_digest) is not None:
                    return False
                raise MlInternSpeechAdapterRepositoryError("speech_adapter_legacy_import_conflict") from exc
            return True

    @staticmethod
    def _enqueue(db: Session, event: SemanticMediaAuditEvent | None) -> None:
        if event is not None:
            SqlSemanticMediaAuditOutbox.enqueue_in_session(db, event)

    @staticmethod
    def _stage_lineage(
        db: Session,
        record: Mapping[str, Any],
        nodes: Sequence[SpeechLineageNode],
        edges: Sequence[SpeechLineageEdge],
    ) -> None:
        if not nodes:
            return
        SpeechEvidenceLineageRepository().stage(
            db,
            tenant_id=str(record["tenant_id"]),
            owner_subject=str(record["owner_subject"]),
            nodes=nodes,
            edges=edges,
            now_ms=int(record["updated_at_ms"]),
        )

    @staticmethod
    def _assert_replay(row: MlInternSpeechAdapterDB, record: Mapping[str, Any]) -> None:
        current = MlInternSpeechAdapterRepository._record(row)
        if any(str(current[field]) != str(record[field]) for field in _IMMUTABLE_FIELDS):
            raise MlInternSpeechAdapterRepositoryError("speech_adapter_id_conflict")

    @staticmethod
    def _row(record: Mapping[str, Any]) -> MlInternSpeechAdapterDB:
        row = MlInternSpeechAdapterDB(
            id=str(record["adapter_id"]),
            version=str(record["version"]),
            tenant_id=str(record["tenant_id"]),
            owner_subject=str(record["owner_subject"]),
            pair_id=str(record["pair_id"]),
            direction=str(record["direction"]),
            speaker_digest=str(record["speaker_digest"]),
            scope_digest=str(record["scope_digest"]),
            base_model_id=str(record["base_model_id"]),
            base_model_digest=str(record["base_model_digest"]),
            backend=str(record["backend"]),
            backend_digest=str(record["backend_digest"]),
            dataset_digest=str(record["dataset_digest"]),
            split_digest=str(record["split_digest"]),
            evaluation_report_digest=str(record["evaluation_report_digest"]),
            evaluation_policy_version=str(record["evaluation_policy_version"]),
            evaluation_passed=bool(record["evaluation_passed"]),
            evaluation_approval_eligible=bool(record["evaluation_approval_eligible"]),
            consent_digest=str(record["consent_digest"]),
            consent_expires_at_ms=int(record["consent_expires_at_ms"]),
            artifact_ref=str(record["artifact_ref"]),
            artifact_sha256=str(record["artifact_sha256"]),
            artifact_size_bytes=int(record["artifact_size_bytes"]),
            expires_at_ms=int(record["expires_at_ms"]),
            status=str(record["status"]),
            registry_version=int(record["registry_version"]),
            created_at_ms=int(record["created_at_ms"]),
            updated_at_ms=int(record["updated_at_ms"]),
        )
        MlInternSpeechAdapterRepository._copy_record(row, record)
        return row

    @staticmethod
    def _copy_record(row: MlInternSpeechAdapterDB, record: Mapping[str, Any]) -> None:
        for field in (
            "version",
            "tenant_id",
            "owner_subject",
            "pair_id",
            "direction",
            "speaker_digest",
            "scope_digest",
            "base_model_id",
            "base_model_digest",
            "backend",
            "backend_digest",
            "dataset_digest",
            "split_digest",
            "evaluation_report_digest",
            "evaluation_policy_version",
            "evaluation_passed",
            "evaluation_approval_eligible",
            "consent_digest",
            "consent_expires_at_ms",
            "artifact_ref",
            "artifact_sha256",
            "artifact_size_bytes",
            "expires_at_ms",
            "status",
            "registry_version",
            "approved_by_digest",
            "approval_reason_code",
            "approved_at_ms",
            "revoked_at_ms",
            "deprecated_at_ms",
            "expired_at_ms",
            "rollback_of_adapter_id",
            "created_at_ms",
            "updated_at_ms",
        ):
            setattr(row, field, record[field])
        row.lineage = [dict(item) for item in record["lineage"]]

    @staticmethod
    def _record(row: MlInternSpeechAdapterDB) -> dict[str, Any]:
        return {
            "adapter_id": row.id,
            "version": row.version,
            "tenant_id": row.tenant_id,
            "owner_subject": row.owner_subject,
            "pair_id": row.pair_id,
            "direction": row.direction,
            "speaker_digest": row.speaker_digest,
            "scope_digest": row.scope_digest,
            "base_model_id": row.base_model_id,
            "base_model_digest": row.base_model_digest,
            "backend": row.backend,
            "backend_digest": row.backend_digest,
            "dataset_digest": row.dataset_digest,
            "split_digest": row.split_digest,
            "evaluation_report_digest": row.evaluation_report_digest,
            "evaluation_policy_version": row.evaluation_policy_version,
            "evaluation_passed": row.evaluation_passed,
            "evaluation_approval_eligible": row.evaluation_approval_eligible,
            "consent_digest": row.consent_digest,
            "consent_expires_at_ms": row.consent_expires_at_ms,
            "artifact_ref": row.artifact_ref,
            "artifact_sha256": row.artifact_sha256,
            "artifact_size_bytes": row.artifact_size_bytes,
            "expires_at_ms": row.expires_at_ms,
            "status": row.status,
            "registry_version": row.registry_version,
            "approved_by_digest": row.approved_by_digest,
            "approval_reason_code": row.approval_reason_code,
            "approved_at_ms": row.approved_at_ms,
            "revoked_at_ms": row.revoked_at_ms,
            "deprecated_at_ms": row.deprecated_at_ms,
            "expired_at_ms": row.expired_at_ms,
            "rollback_of_adapter_id": row.rollback_of_adapter_id,
            "created_at_ms": row.created_at_ms,
            "updated_at_ms": row.updated_at_ms,
            "lineage": [dict(item) for item in row.lineage or []],
        }


__all__ = [
    "MlInternSpeechAdapterRepository",
    "MlInternSpeechAdapterRepositoryError",
    "SpeechAdapterCasMutation",
]
