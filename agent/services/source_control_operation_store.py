"""Durable idempotency/operation receipts and bulk checkpoints for Source Control mutations.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Mapping

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control import (
    SourceControlBulkTargetCheckpointDB,
    SourceControlOperationDB,
)
from agent.services.source_control_bulk_service import (
    BulkIdempotencyClaim,
    BulkTargetCheckpoint,
)
from agent.services.source_control_runtime_support import (
    SourceControlApiRuntimeError,
)


class SQLSourceControlOperationStore:
    """Lease/reclaim operation claim plus durable per-target checkpoints."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock=time.time,
        lease_seconds: float = 60.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("idempotency_lease_invalid")
        self._engine = engine
        self._clock = clock
        self._lease_seconds = float(lease_seconds)

    def claim(
        self, *, idempotency_key: str, plan_digest: str
    ) -> BulkIdempotencyClaim:
        now = float(self._clock())
        token = secrets.token_urlsafe(32)
        with Session(self._engine) as db:
            db.add(
                SourceControlOperationDB(
                    idempotency_key=idempotency_key,
                    request_digest=plan_digest,
                    operation="source_control_mutation",
                    state="claimed",
                    claim_token=token,
                    lease_expires_at_epoch=now + self._lease_seconds,
                    lock_version=1,
                    created_at_epoch=now,
                    updated_at_epoch=now,
                )
            )
            try:
                db.commit()
                return BulkIdempotencyClaim(
                    state="claimed",
                    claim_token=token,
                    lease_expires_at_epoch=now + self._lease_seconds,
                )
            except IntegrityError:
                db.rollback()
                row = db.get(SourceControlOperationDB, idempotency_key)
                if row is None:
                    raise SourceControlApiRuntimeError(
                        "idempotency_claim_failed", status_code=409
                    )
                if row.request_digest != plan_digest:
                    raise SourceControlApiRuntimeError(
                        "idempotency_key_conflict", status_code=409
                    )
                if row.state == "completed":
                    if not row.result_json:
                        raise SourceControlApiRuntimeError(
                            "idempotency_result_missing", status_code=500
                        )
                    return BulkIdempotencyClaim(
                        state="completed",
                        result=json.loads(row.result_json),
                    )
                if float(row.lease_expires_at_epoch or 0) <= now:
                    mutation = db.exec(
                        update(SourceControlOperationDB)
                        .where(
                            SourceControlOperationDB.idempotency_key
                            == idempotency_key,
                            SourceControlOperationDB.request_digest
                            == plan_digest,
                            SourceControlOperationDB.state == "claimed",
                            SourceControlOperationDB.lock_version
                            == row.lock_version,
                        )
                        .values(
                            claim_token=token,
                            lease_expires_at_epoch=now
                            + self._lease_seconds,
                            lock_version=row.lock_version + 1,
                            updated_at_epoch=now,
                        )
                    )
                    if mutation.rowcount == 1:
                        db.commit()
                        return BulkIdempotencyClaim(
                            state="claimed",
                            claim_token=token,
                            lease_expires_at_epoch=now
                            + self._lease_seconds,
                            checkpoints=self._checkpoints(
                                idempotency_key=idempotency_key,
                                plan_digest=plan_digest,
                            ),
                        )
                    db.rollback()
                return BulkIdempotencyClaim(state="in_progress")

    def complete(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str | None = None,
        result: Mapping[str, object],
    ) -> None:
        with Session(self._engine) as db:
            owner_filters = []
            if claim_token is not None:
                owner_filters.append(
                    SourceControlOperationDB.claim_token == claim_token
                )
            mutation = db.exec(
                update(SourceControlOperationDB)
                .where(
                    SourceControlOperationDB.idempotency_key
                    == idempotency_key,
                    SourceControlOperationDB.request_digest == plan_digest,
                    SourceControlOperationDB.state == "claimed",
                    *owner_filters,
                )
                .values(
                    state="completed",
                    result_json=json.dumps(
                        dict(result),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                    lease_expires_at_epoch=None,
                    updated_at_epoch=float(self._clock()),
                )
            )
            if mutation.rowcount != 1:
                db.rollback()
                raise SourceControlApiRuntimeError(
                    "idempotency_completion_conflict", status_code=409
                )
            db.commit()

    def release(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
    ) -> None:
        """Expire an owned claim so a failed operation can be retried safely."""

        now = float(self._clock())
        with Session(self._engine) as db:
            mutation = db.exec(
                update(SourceControlOperationDB)
                .where(
                    SourceControlOperationDB.idempotency_key
                    == idempotency_key,
                    SourceControlOperationDB.request_digest == plan_digest,
                    SourceControlOperationDB.state == "claimed",
                    SourceControlOperationDB.claim_token == claim_token,
                )
                .values(
                    lease_expires_at_epoch=now,
                    updated_at_epoch=now,
                )
            )
            if mutation.rowcount != 1:
                db.rollback()
                raise SourceControlApiRuntimeError(
                    "idempotency_release_conflict", status_code=409
                )
            db.commit()

    def begin_target(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
        target_ordinal: int,
        resource_id: str,
        target_digest: str,
    ) -> BulkTargetCheckpoint:
        now = float(self._clock())
        checkpoint_id = "bcp_" + hashlib.sha256(
            f"{idempotency_key}\0{target_ordinal}\0{target_digest}".encode(
                "utf-8"
            )
        ).hexdigest()
        with Session(self._engine) as db:
            self._renew_owner(
                db,
                idempotency_key=idempotency_key,
                plan_digest=plan_digest,
                claim_token=claim_token,
                now=now,
            )
            existing = db.get(
                SourceControlBulkTargetCheckpointDB, checkpoint_id
            )
            if existing is None:
                existing = SourceControlBulkTargetCheckpointDB(
                    checkpoint_id=checkpoint_id,
                    idempotency_key=idempotency_key,
                    plan_digest=plan_digest,
                    target_ordinal=target_ordinal,
                    resource_id=resource_id,
                    target_digest=target_digest,
                    state="executing",
                    attempt_count=1,
                    created_at_epoch=now,
                    updated_at_epoch=now,
                )
                db.add(existing)
            elif (
                existing.idempotency_key != idempotency_key
                or existing.plan_digest != plan_digest
                or existing.target_ordinal != target_ordinal
                or existing.resource_id != resource_id
                or existing.target_digest != target_digest
            ):
                raise SourceControlApiRuntimeError(
                    "bulk_target_checkpoint_conflict", status_code=409
                )
            else:
                existing.attempt_count += 1
                existing.updated_at_epoch = now
                db.add(existing)
            db.commit()
            db.refresh(existing)
            return self._checkpoint(existing)

    def complete_target(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
        target_ordinal: int,
        target_digest: str,
        result: Mapping[str, object],
    ) -> BulkTargetCheckpoint:
        now = float(self._clock())
        encoded = json.dumps(
            dict(result),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        with Session(self._engine) as db:
            self._renew_owner(
                db,
                idempotency_key=idempotency_key,
                plan_digest=plan_digest,
                claim_token=claim_token,
                now=now,
            )
            row = db.exec(
                select(SourceControlBulkTargetCheckpointDB).where(
                    SourceControlBulkTargetCheckpointDB.idempotency_key
                    == idempotency_key,
                    SourceControlBulkTargetCheckpointDB.plan_digest
                    == plan_digest,
                    SourceControlBulkTargetCheckpointDB.target_ordinal
                    == target_ordinal,
                    SourceControlBulkTargetCheckpointDB.target_digest
                    == target_digest,
                )
            ).first()
            if row is None:
                raise SourceControlApiRuntimeError(
                    "bulk_target_checkpoint_missing", status_code=409
                )
            if row.state == "completed":
                if row.result_json != encoded:
                    raise SourceControlApiRuntimeError(
                        "bulk_target_result_conflict", status_code=409
                    )
                return self._checkpoint(row)
            row.state = "completed"
            row.result_json = encoded
            row.updated_at_epoch = now
            db.add(row)
            db.commit()
            db.refresh(row)
            return self._checkpoint(row)

    def _checkpoints(
        self, *, idempotency_key: str, plan_digest: str
    ) -> tuple[BulkTargetCheckpoint, ...]:
        with Session(self._engine) as db:
            rows = db.exec(
                select(SourceControlBulkTargetCheckpointDB)
                .where(
                    SourceControlBulkTargetCheckpointDB.idempotency_key
                    == idempotency_key,
                    SourceControlBulkTargetCheckpointDB.plan_digest
                    == plan_digest,
                )
                .order_by(
                    SourceControlBulkTargetCheckpointDB.target_ordinal
                )
            ).all()
            return tuple(self._checkpoint(row) for row in rows)

    def _renew_owner(
        self,
        db: Session,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
        now: float,
    ) -> None:
        mutation = db.exec(
            update(SourceControlOperationDB)
            .where(
                SourceControlOperationDB.idempotency_key == idempotency_key,
                SourceControlOperationDB.request_digest == plan_digest,
                SourceControlOperationDB.state == "claimed",
                SourceControlOperationDB.claim_token == claim_token,
                SourceControlOperationDB.lease_expires_at_epoch > now,
            )
            .values(
                lease_expires_at_epoch=now + self._lease_seconds,
                updated_at_epoch=now,
            )
        )
        if mutation.rowcount != 1:
            db.rollback()
            raise SourceControlApiRuntimeError(
                "idempotency_claim_lost", status_code=409
            )

    @staticmethod
    def _checkpoint(
        row: SourceControlBulkTargetCheckpointDB,
    ) -> BulkTargetCheckpoint:
        return BulkTargetCheckpoint(
            target_ordinal=int(row.target_ordinal),
            resource_id=row.resource_id,
            target_digest=row.target_digest,
            state=row.state,
            result=(
                json.loads(row.result_json) if row.result_json else None
            ),
        )


__all__ = ["SQLSourceControlOperationStore"]
