"""Durable ordering, replay and fencing repository for TURN observations."""

from __future__ import annotations

import time

from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.turn_observations import TurnObservationCursorDB, TurnObservationReplayDB


class TurnObservationRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SqlTurnObservationCursorRepository:
    def __init__(self, *, db_engine) -> None:
        self._engine = db_engine

    def count_recent(self, *, pool_id: str, instance_id: str, since: float) -> int:
        with Session(self._engine) as db:
            return int(
                db.exec(
                    select(func.count())
                    .select_from(TurnObservationReplayDB)
                    .where(
                        TurnObservationReplayDB.pool_id == pool_id,
                        TurnObservationReplayDB.instance_id == instance_id,
                        TurnObservationReplayDB.accepted_at >= since,
                    )
                ).one()
            )

    def ingest(
        self,
        *,
        document: dict,
        normalized: dict,
        payload_digest: str,
        observation_id_digest: str,
        boot_id_digest: str,
        observer_identity_id: str,
        observer_identity_version: int,
        now: float,
        replay_ttl_seconds: int,
        retention_seconds: int,
        replay_entries_max: int,
        retired_boot_ids_max: int,
    ) -> tuple[str, TurnObservationCursorDB, tuple[str, ...]]:
        pool_id = document["pool_id"]
        instance_id = document["instance_id"]
        with Session(self._engine, expire_on_commit=False) as db:
            db.exec(delete(TurnObservationReplayDB).where(TurnObservationReplayDB.expires_at <= now))
            replay = db.exec(
                select(TurnObservationReplayDB).where(
                    TurnObservationReplayDB.observation_id_digest == observation_id_digest
                )
            ).first()
            cursor = db.exec(
                select(TurnObservationCursorDB)
                .where(
                    TurnObservationCursorDB.pool_id == pool_id,
                    TurnObservationCursorDB.instance_id == instance_id,
                )
                .with_for_update()
            ).first()
            if replay is not None:
                if replay.payload_digest != payload_digest:
                    raise TurnObservationRepositoryError("turn_observation_replay_conflict")
                if cursor is None:
                    raise TurnObservationRepositoryError("turn_observation_cursor_missing")
                return "duplicate", cursor, ("turn_observation_duplicate",)
            reasons: list[str] = []
            sequence = int(document["sequence"])
            boot_id = str(document["boot_id"])
            measured_at = float(document["measured_at_seconds"])
            counters = dict(document["counters"])
            if cursor is None:
                if sequence != 1:
                    raise TurnObservationRepositoryError("turn_observation_initial_sequence_invalid")
                cursor = TurnObservationCursorDB(
                    pool_id=pool_id,
                    instance_id=instance_id,
                    observer_identity_id=observer_identity_id,
                    observer_identity_version=observer_identity_version,
                    current_boot_id=boot_id,
                    retired_boot_ids=[],
                    highest_sequence=sequence,
                    last_payload_digest=payload_digest,
                    last_observation_id=document["observation_id"],
                    last_measured_at=measured_at,
                    last_counters_json=counters,
                    normalized_observation_json=normalized,
                    health_status=normalized["health_status"],
                    capacity_status=normalized["capacity_status"],
                    fencing_token=1,
                    version=1,
                    fresh_until=normalized["fresh_until"],
                    retain_until=now + retention_seconds,
                    created_at=now,
                    updated_at=now,
                )
                db.add(cursor)
            else:
                if (
                    cursor.observer_identity_id != observer_identity_id
                    or cursor.observer_identity_version > observer_identity_version
                ):
                    raise TurnObservationRepositoryError("turn_observation_identity_fence_stale")
                same_boot = boot_id == cursor.current_boot_id
                if not same_boot:
                    if boot_id in cursor.retired_boot_ids or sequence != 1 or measured_at <= cursor.last_measured_at:
                        raise TurnObservationRepositoryError("turn_observation_boot_replay")
                    retired = (list(cursor.retired_boot_ids) + [cursor.current_boot_id])[-retired_boot_ids_max:]
                    cursor.retired_boot_ids = retired
                    cursor.current_boot_id = boot_id
                    cursor.fencing_token += 1
                    reasons.append("turn_observation_boot_changed")
                elif sequence <= cursor.highest_sequence:
                    raise TurnObservationRepositoryError("turn_observation_sequence_replay")
                elif sequence > cursor.highest_sequence + 1:
                    reasons.append("turn_observation_sequence_gap")
                    normalized["capacity_status"] = "stop"
                if same_boot and self._regressed(cursor.last_counters_json, counters):
                    reasons.append("turn_observation_counter_regression")
                    normalized["capacity_status"] = "stop"
                cursor.observer_identity_version = observer_identity_version
                cursor.highest_sequence = sequence
                cursor.last_payload_digest = payload_digest
                cursor.last_observation_id = document["observation_id"]
                cursor.last_measured_at = measured_at
                cursor.last_counters_json = counters
                cursor.normalized_observation_json = normalized
                cursor.health_status = normalized["health_status"]
                cursor.capacity_status = normalized["capacity_status"]
                cursor.version += 1
                cursor.fresh_until = normalized["fresh_until"]
                cursor.retain_until = now + retention_seconds
                cursor.updated_at = now
                db.add(cursor)
            count = int(
                db.exec(
                    select(func.count())
                    .select_from(TurnObservationReplayDB)
                    .where(
                        TurnObservationReplayDB.pool_id == pool_id,
                        TurnObservationReplayDB.instance_id == instance_id,
                    )
                ).one()
            )
            if count >= replay_entries_max:
                oldest = db.exec(
                    select(TurnObservationReplayDB)
                    .where(
                        TurnObservationReplayDB.pool_id == pool_id,
                        TurnObservationReplayDB.instance_id == instance_id,
                    )
                    .order_by(TurnObservationReplayDB.accepted_at)
                    .limit(count - replay_entries_max + 1)
                ).all()
                for item in oldest:
                    db.delete(item)
            db.add(
                TurnObservationReplayDB(
                    pool_id=pool_id,
                    instance_id=instance_id,
                    observation_id_digest=observation_id_digest,
                    payload_digest=payload_digest,
                    boot_id_digest=boot_id_digest,
                    sequence=sequence,
                    accepted_at=now,
                    expires_at=now + replay_ttl_seconds,
                )
            )
            try:
                db.commit()
                db.refresh(cursor)
            except IntegrityError as exc:
                db.rollback()
                raise TurnObservationRepositoryError("turn_observation_cas_conflict") from exc
            if not reasons:
                reasons.append("turn_observation_accepted")
            return "accepted", cursor, tuple(reasons)

    @staticmethod
    def _regressed(previous: dict, current: dict) -> bool:
        for name, previous_value in previous.items():
            value = current.get(name)
            if isinstance(previous_value, int) and isinstance(value, int) and value < previous_value:
                return True
        return False


__all__ = ["SqlTurnObservationCursorRepository", "TurnObservationRepositoryError"]
