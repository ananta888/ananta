from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from sqlmodel import Session

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceDB
from agent.db_models.tasks import TaskDB
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.semantic_media_canary_scan_service import SemanticMediaCanaryScanService
from agent.services.semantic_relay_observability import SemanticRelayObservability
from tests.speech_evidence_support import stored_evidence

CANARIES = (
    b"KNOWN-AUDIO-CANARY-9F3A",
    b"KNOWN-TRANSCRIPT-CANARY-7B2D",
    b"KNOWN-KEY-CANARY-4C1E",
)


@dataclass(frozen=True)
class _Surface:
    name: str
    reader: Callable[[], Iterable[bytes | str]]

    def chunks(self) -> Iterable[bytes | str]:
        return self.reader()


def _database_chunks(row: SpeechEvidenceDB) -> tuple[str, ...]:
    return tuple(
        json.dumps(getattr(row, column.name), default=repr, sort_keys=True)
        for column in row.__table__.columns
    )


def test_canary_scan_reads_real_encrypted_db_audit_task_artifact_log_and_metric_surfaces(
    tmp_path: Path,
    caplog,
) -> None:
    payload = b"\n".join(CANARIES)
    _, _, consent, evidence = stored_evidence("privacy-canary", payload)
    with Session(engine) as session:
        evidence_row = session.get(SpeechEvidenceDB, evidence.evidence_id)
        assert evidence_row is not None
        session.expunge(evidence_row)

    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: 1_000_000),
        secret=b"a" * 32,
    )
    audit.record_transition(
        idempotency_key="privacy-canary-audit",
        tenant_id=CANARIES[1].decode(),
        scope=f"semantic-media-session:{consent.session_id}",
        event_type="speech_evidence",
        transition="quarantined",
        reason_code="speech_evidence_quarantined",
        epoch=consent.session_epoch,
        job_ref=evidence.evidence_id,
        retention_ms=3_600_000,
    )
    audit_rows, _ = audit_repository.page(
        tenant_digest=audit.digest("tenant", CANARIES[1].decode()),
        scope_digest=audit.digest("scope", f"semantic-media-session:{consent.session_id}"),
        after_event_id=None,
        limit=10,
        now_ms=1_000_000,
    )

    task_id = f"privacy-canary-task-{hashlib.sha256(payload).hexdigest()[:16]}"
    with Session(engine) as session:
        session.add(
            TaskDB(
                id=task_id,
                title="Hub privacy lifecycle fence",
                description="Apply one bounded revocation transition.",
                status="assigned",
                task_kind="speech_privacy",
                worker_execution_context={
                    "evidence_digest": hashlib.sha256(payload).hexdigest(),
                    "execution_owner": "worker",
                    "orchestration_owner": "hub",
                },
            )
        )
        session.commit()
        task = session.get(TaskDB, task_id)
        assert task is not None
        session.expunge(task)

    artifact = tmp_path / "speech-evidence-canary.enc"
    artifact.write_bytes(bytes(evidence_row.nonce) + bytes(evidence_row.ciphertext))

    metric_rows: list[dict[str, object]] = []
    SemanticRelayObservability(metric_rows.append).emit(
        direction="outbound",
        traffic_class="evidence_bulk",
        state="queued",
        reason_code="accepted",
        scope_digest=hashlib.sha256(consent.session_id.encode()).hexdigest(),
    )
    caplog.set_level(logging.INFO)
    logging.getLogger("ananta.semantic-media.privacy").info(
        "privacy transition reason=speech_evidence_quarantined scope=%s",
        hashlib.sha256(consent.session_id.encode()).hexdigest(),
    )

    surfaces = (
        _Surface("db", lambda: _database_chunks(evidence_row)),
        _Surface("audit", lambda: tuple(json.dumps(row.public(), sort_keys=True) for row in audit_rows)),
        _Surface("task", lambda: (json.dumps(task.model_dump(), default=repr, sort_keys=True),)),
        _Surface("artifact", lambda: (artifact.read_bytes(),)),
        _Surface("metric", lambda: tuple(json.dumps(row, sort_keys=True) for row in metric_rows)),
        _Surface("log", lambda: tuple(record.getMessage() for record in caplog.records)),
    )
    summary = SemanticMediaCanaryScanService().scan(
        canaries=CANARIES,
        surfaces=surfaces,
        required_surfaces=frozenset({"log", "db", "audit", "task", "artifact", "metric"}),
    )
    assert summary.surface_count == 6
    assert summary.chunk_count >= 6
    assert len(summary.surface_digest) == 64
