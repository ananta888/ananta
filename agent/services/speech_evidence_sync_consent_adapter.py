"""Durable consent projection for bilateral speech-evidence sync.

Extracted from ``agent.services.speech_evidence_sync_composition`` (SRP): the
adapter reads governance consent rows and projects them into the narrower
``HubEvidenceConsent`` scope used by offer and sync authorization.
"""

from __future__ import annotations

import time
from typing import Mapping

from sqlalchemy import or_
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
from agent.services.speech_evidence_offer_service import HubEvidenceConsent
from agent.services.speech_evidence_sync_share_session_adapters import ShareSessionSpeechEvidenceEpoch


class SqlSpeechEvidenceConsentAdapter:
    """Project durable governance consent into the narrower sync scope."""

    def __init__(self, epochs: ShareSessionSpeechEvidenceEpoch, *, clock_ms=lambda: time.time_ns() // 1_000_000):
        self._epochs = epochs
        self._clock_ms = clock_ms

    def current(self, *, pair_id: str, peer_id: str) -> HubEvidenceConsent | None:
        """Legacy port: resolve only when the pair is globally unambiguous."""

        with Session(engine) as session:
            scopes = session.exec(
                select(
                    SpeechEvidenceConsentDB.tenant_id,
                    SpeechEvidenceConsentDB.session_id,
                )
                .where(
                    SpeechEvidenceConsentDB.pair_id == pair_id,
                    or_(
                        SpeechEvidenceConsentDB.speaker_id == peer_id,
                        SpeechEvidenceConsentDB.recipient_id == peer_id,
                    ),
                )
                .distinct()
            ).all()
        if len(scopes) != 1:
            return None
        tenant_id, session_id = scopes[0]
        return self.current_scoped(
            tenant_id=str(tenant_id),
            session_id=str(session_id),
            pair_id=pair_id,
            peer_id=peer_id,
        )

    def current_scoped(
        self,
        *,
        tenant_id: str,
        session_id: str,
        pair_id: str,
        peer_id: str,
    ) -> HubEvidenceConsent | None:
        now = int(self._clock_ms())
        epoch = self._epochs.current_epoch(session_id=session_id, pair_id=pair_id)
        if epoch is None:
            return None
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechEvidenceConsentDB).where(
                    SpeechEvidenceConsentDB.tenant_id == tenant_id,
                    SpeechEvidenceConsentDB.session_id == session_id,
                    SpeechEvidenceConsentDB.pair_id == pair_id,
                    SpeechEvidenceConsentDB.session_epoch == epoch,
                    SpeechEvidenceConsentDB.state == "active",
                    SpeechEvidenceConsentDB.expires_at_ms > now,
                    or_(
                        SpeechEvidenceConsentDB.speaker_id == peer_id,
                        SpeechEvidenceConsentDB.recipient_id == peer_id,
                    ),
                )
            ).all()
        owned = [row for row in rows if row.owner_subject == peer_id]
        candidates = owned if owned else rows
        if len(candidates) != 1:
            return None
        return self._project(candidates[0], peer_id)

    @staticmethod
    def _project(row: SpeechEvidenceConsentDB, peer_id: str) -> HubEvidenceConsent | None:
        scope = dict(row.scope_payload or {})
        grants = scope.get("grants")
        if not isinstance(grants, Mapping):
            return None
        participants = {str(row.speaker_id), str(row.recipient_id)}
        if (
            row.direction == "local"
            or set(str(value) for value in row.required_signers) != participants
            or set(str(value) for value in row.signature_digests) != participants
        ):
            return None
        raw_classes = {str(value) for value in scope.get("data_classes", ()) if isinstance(value, str)}
        data_classes: set[str] = set()
        fields: set[str] = set()
        if grants.get("transcript_share") is True:
            if "transcript" in raw_classes:
                data_classes.update({"transcript", "text_corrections"})
                fields.update({"transcript", "timing", "confidence"})
            if "correction" in raw_classes:
                data_classes.update({"correction", "text_corrections", "vocabulary"})
                fields.update({"transcript", "timing", "confidence"})
        if grants.get("feature_share") is True:
            for name in ("acoustic_features", "speaker_embedding", "quality_metrics"):
                if name in raw_classes:
                    data_classes.add(name)
                    fields.add(name)
            if data_classes & {"acoustic_features", "speaker_embedding", "quality_metrics"}:
                fields.add("timing")
        if grants.get("raw_audio_share") is True and "audio" in raw_classes:
            data_classes.add("raw_audio")
            fields.add("audio")
        if not data_classes:
            return None
        trainer_classes = {"none"}
        if grants.get("dataset_import") is True and grants.get("training") is True:
            trainer_classes.add("speech_adaptation")
        retention = scope.get("retention_seconds", row.scope_payload.get("retention_seconds"))
        if type(retention) is not int or retention < 1:
            return None
        return HubEvidenceConsent(
            peer_id=peer_id,
            speaker_id=str(row.speaker_id),
            pair_id=str(row.pair_id),
            version=int(row.consent_version),
            digest=str(row.consent_digest),
            directions=frozenset({str(row.direction)}),
            purposes=frozenset({str(row.purpose)}),
            data_classes=frozenset(data_classes),
            fields=frozenset(fields),
            trainer_classes=frozenset(trainer_classes),
            maximum_retention_seconds=retention,
            expires_at_ms=int(row.expires_at_ms),
            active=True,
        )


__all__ = ["SqlSpeechEvidenceConsentAdapter"]
