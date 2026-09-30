"""Database-backed sliding replay window shared by all Hub replicas."""

from __future__ import annotations

import hashlib
import time

from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence_sync import SpeechEvidenceReplayStateDB
from ananta_contracts.speech_evidence_sync import MAX_SEQUENCE, SpeechEvidenceProtocolError


class SqlSpeechEvidenceReplayWindow:
    """Database-backed implementation of the verifier's replay-window port."""

    def __init__(
        self,
        *,
        width: int = 256,
        maximum_contexts: int = 2048,
        ttl_ms: int = 60 * 60 * 1000,
        clock_ms=lambda: time.time_ns() // 1_000_000,
    ) -> None:
        if not 32 <= width <= 4096 or not 1 <= maximum_contexts <= 100_000 or ttl_ms < 60_000:
            raise ValueError("speech_replay_policy_invalid")
        self._width = width
        self._maximum = maximum_contexts
        self._ttl_ms = ttl_ms
        self._clock_ms = clock_ms

    def check(self, key: tuple[str, str, str, int, str], sequence: int) -> str | None:
        now = int(self._clock_ms())
        with Session(engine) as session:
            row = session.get(SpeechEvidenceReplayStateDB, _replay_id(key))
            if row is None or row.expires_at_ms <= now:
                return None
            return _replay_reason(int(row.highest_sequence), int(row.bitmap_hex, 16), self._width, sequence)

    def commit(self, key: tuple[str, str, str, int, str], sequence: int) -> None:
        if type(sequence) is not int or not 1 <= sequence <= MAX_SEQUENCE:
            raise SpeechEvidenceProtocolError("speech_evidence_sequence_invalid")
        now = int(self._clock_ms())
        identifier = _replay_id(key)
        try:
            with Session(engine) as session:
                session.exec(
                    delete(SpeechEvidenceReplayStateDB).where(SpeechEvidenceReplayStateDB.expires_at_ms <= now)
                )
                row = session.exec(
                    select(SpeechEvidenceReplayStateDB)
                    .where(SpeechEvidenceReplayStateDB.id == identifier)
                    .with_for_update()
                ).first()
                if row is None:
                    contexts = int(session.exec(select(func.count(SpeechEvidenceReplayStateDB.id))).one())
                    if contexts >= self._maximum:
                        raise SpeechEvidenceProtocolError("speech_evidence_replay_state_exhausted")
                    session.add(
                        SpeechEvidenceReplayStateDB(
                            id=identifier,
                            session_id=key[0],
                            pair_id=key[1],
                            sender_id=key[2],
                            epoch=key[3],
                            traffic_class=key[4],
                            highest_sequence=sequence,
                            bitmap_hex="1",
                            width=self._width,
                            expires_at_ms=now + self._ttl_ms,
                            updated_at_ms=now,
                        )
                    )
                else:
                    reason = _replay_reason(int(row.highest_sequence), int(row.bitmap_hex, 16), self._width, sequence)
                    if reason is not None:
                        raise SpeechEvidenceProtocolError(reason)
                    bitmap = int(row.bitmap_hex, 16)
                    highest = int(row.highest_sequence)
                    if sequence > highest:
                        bitmap = ((bitmap << (sequence - highest)) | 1) & ((1 << self._width) - 1)
                        highest = sequence
                    else:
                        bitmap |= 1 << (highest - sequence)
                    row.highest_sequence = highest
                    row.bitmap_hex = format(bitmap, "x")
                    row.expires_at_ms = now + self._ttl_ms
                    row.version += 1
                    row.updated_at_ms = now
                    session.add(row)
                session.commit()
        except IntegrityError as exc:
            # A competing Hub inserted the same context. Its committed claim
            # is authoritative; never reinterpret this request as fresh.
            reason = self.check(key, sequence) or "speech_evidence_replayed"
            raise SpeechEvidenceProtocolError(reason) from exc

    def advance_epoch(self, *, session_id: str, pair_id: str, minimum_epoch: int) -> None:
        if minimum_epoch < 1:
            raise ValueError("speech_replay_epoch_invalid")
        with Session(engine) as session:
            session.exec(
                delete(SpeechEvidenceReplayStateDB).where(
                    SpeechEvidenceReplayStateDB.session_id == session_id,
                    SpeechEvidenceReplayStateDB.pair_id == pair_id,
                    SpeechEvidenceReplayStateDB.epoch < minimum_epoch,
                )
            )
            session.commit()


def _replay_id(key: tuple[str, str, str, int, str]) -> str:
    return hashlib.sha256("\0".join(map(str, key)).encode()).hexdigest()


def _replay_reason(highest: int, bitmap: int, width: int, sequence: int) -> str | None:
    if sequence > highest:
        return None
    offset = highest - sequence
    if offset >= width:
        return "speech_evidence_sequence_stale"
    if bitmap & (1 << offset):
        return "speech_evidence_replayed"
    return None


__all__ = ["SqlSpeechEvidenceReplayWindow"]
