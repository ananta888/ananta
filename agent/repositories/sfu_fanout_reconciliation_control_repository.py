"""SQL lease, checkpoint and outcome store for SFU fanout route reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Callable

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_hub_control import (
    SfuFanoutReconciliationControlDB,
    SfuFanoutReconciliationOutcomeDB,
)
from agent.models.sfu_route_reconciliation import (
    ReconciliationPhase,
    RouteReconciliationCursor,
    RouteReconciliationItemOutcome,
    RouteReconciliationLease,
    RouteReconciliationScope,
)
from agent.repositories.sfu_hub_control_validation import (
    _plain_digest,
    _positive_clock_ms,
    _safe_reason,
    _safe_ref,
    SfuHubControlRepositoryError,
)


class SqlSfuFanoutReconciliationControlRepository:
    """Lease, checkpoint and content-free outcome store for one tenant room."""

    def __init__(
        self,
        *,
        owner_digest_secret: bytes,
        db_engine=default_engine,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        control_retention_ms: int = 86_400_000,
        outcome_retention_ms: int = 3_600_000,
        outcomes_max_per_scope: int = 10_000,
        purge_batch: int = 128,
    ) -> None:
        if (
            len(owner_digest_secret) < 32
            or not 60_000 <= control_retention_ms <= 604_800_000
            or not 60_000 <= outcome_retention_ms <= 86_400_000
            or not 1 <= outcomes_max_per_scope <= 1_000_000
            or not 1 <= purge_batch <= 1_000
        ):
            raise ValueError("sfu_reconciliation_repository_limits_invalid")
        self._secret = bytes(owner_digest_secret)
        self._engine = db_engine
        self._clock_ms = clock_ms
        self._control_retention = control_retention_ms
        self._outcome_retention = outcome_retention_ms
        self._outcome_capacity = outcomes_max_per_scope
        self._purge_batch = purge_batch

    def acquire(
        self,
        *,
        scope: RouteReconciliationScope,
        owner_ref: str,
        now_ms: int,
        lease_ttl_ms: int,
    ) -> RouteReconciliationLease | None:
        _validate_reconciliation_scope(scope, owner_ref)
        if (
            type(now_ms) is not int
            or now_ms <= 0
            or type(lease_ttl_ms) is not int
            or not 100 <= lease_ttl_ms <= 300_000
        ):
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_lease_input_invalid"
            )
        owner_digest = self._owner_digest(owner_ref)
        try:
            with Session(self._engine) as db:
                current = _find_reconciliation_control(db, scope)
                if current is not None and current.lease_expires_at_ms > now_ms:
                    return None
                expires = now_ms + lease_ttl_ms
                if current is None:
                    token = 1
                    db.add(
                        SfuFanoutReconciliationControlDB(
                            id=_reconciliation_control_id(scope),
                            tenant_id=scope.tenant_ref,
                            room_id=scope.room_ref,
                            owner_digest=owner_digest,
                            fencing_token=token,
                            lease_expires_at_ms=expires,
                            version=1,
                            retain_until_ms=now_ms + self._control_retention,
                            created_at=now_ms / 1000.0,
                            updated_at=now_ms / 1000.0,
                        )
                    )
                else:
                    token = current.fencing_token + 1
                    updated = db.exec(
                        sa.update(SfuFanoutReconciliationControlDB)
                        .where(
                            SfuFanoutReconciliationControlDB.id == current.id,
                            SfuFanoutReconciliationControlDB.version
                            == current.version,
                            SfuFanoutReconciliationControlDB.lease_expires_at_ms
                            <= now_ms,
                        )
                        .values(
                            owner_digest=owner_digest,
                            fencing_token=token,
                            lease_expires_at_ms=expires,
                            version=current.version + 1,
                            retain_until_ms=now_ms
                            + self._control_retention,
                            updated_at=now_ms / 1000.0,
                        )
                    )
                    if int(updated.rowcount or 0) != 1:
                        db.rollback()
                        return None
                db.commit()
                return RouteReconciliationLease(
                    scope, owner_ref, str(token), expires
                )
        except IntegrityError:
            return None
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def release(self, lease: RouteReconciliationLease) -> None:
        now_ms = _positive_clock_ms(self._clock_ms())
        token = _lease_token(lease)
        try:
            with Session(self._engine) as db:
                updated = db.exec(
                    sa.update(SfuFanoutReconciliationControlDB)
                    .where(
                        SfuFanoutReconciliationControlDB.tenant_id
                        == lease.scope.tenant_ref,
                        SfuFanoutReconciliationControlDB.room_id
                        == lease.scope.room_ref,
                        SfuFanoutReconciliationControlDB.owner_digest
                        == self._owner_digest(lease.owner_ref),
                        SfuFanoutReconciliationControlDB.fencing_token
                        == token,
                    )
                    .values(
                        owner_digest="",
                        lease_expires_at_ms=now_ms,
                        version=SfuFanoutReconciliationControlDB.version + 1,
                        retain_until_ms=now_ms + self._control_retention,
                        updated_at=now_ms / 1000.0,
                    )
                )
                if int(updated.rowcount or 0) != 1:
                    db.rollback()
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_lease_stale"
                    )
                db.commit()
        except SfuHubControlRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def save(
        self,
        *,
        lease: RouteReconciliationLease,
        cursor: RouteReconciliationCursor | None,
    ) -> None:
        now_ms = _positive_clock_ms(self._clock_ms())
        token = _lease_token(lease)
        if cursor is not None and (
            not isinstance(cursor.phase, ReconciliationPhase)
            or (
                cursor.token is not None
                and (
                    not isinstance(cursor.token, str)
                    or len(cursor.token.encode("utf-8")) > 512
                )
            )
        ):
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_checkpoint_invalid"
            )
        try:
            with Session(self._engine) as db:
                updated = db.exec(
                    sa.update(SfuFanoutReconciliationControlDB)
                    .where(
                        SfuFanoutReconciliationControlDB.tenant_id
                        == lease.scope.tenant_ref,
                        SfuFanoutReconciliationControlDB.room_id
                        == lease.scope.room_ref,
                        SfuFanoutReconciliationControlDB.owner_digest
                        == self._owner_digest(lease.owner_ref),
                        SfuFanoutReconciliationControlDB.fencing_token
                        == token,
                        SfuFanoutReconciliationControlDB.lease_expires_at_ms
                        > now_ms,
                    )
                    .values(
                        checkpoint_phase=(
                            cursor.phase.value if cursor is not None else None
                        ),
                        checkpoint_token=(
                            cursor.token if cursor is not None else None
                        ),
                        version=SfuFanoutReconciliationControlDB.version + 1,
                        retain_until_ms=now_ms + self._control_retention,
                        updated_at=now_ms / 1000.0,
                    )
                )
                if int(updated.rowcount or 0) != 1:
                    db.rollback()
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_lease_stale"
                    )
                db.commit()
        except SfuHubControlRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def load_checkpoint(
        self, *, scope: RouteReconciliationScope
    ) -> RouteReconciliationCursor | None:
        _validate_reconciliation_scope(scope, "checkpoint-reader")
        try:
            with Session(self._engine) as db:
                row = _find_reconciliation_control(db, scope)
                if row is None or row.checkpoint_phase is None:
                    return None
                try:
                    phase = ReconciliationPhase(row.checkpoint_phase)
                except ValueError as exc:
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_checkpoint_corrupt"
                    ) from exc
                return RouteReconciliationCursor(
                    phase, row.checkpoint_token
                )
        except SfuHubControlRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def record(
        self,
        *,
        lease: RouteReconciliationLease,
        outcome: RouteReconciliationItemOutcome,
    ) -> None:
        now_ms = _positive_clock_ms(self._clock_ms())
        token = _lease_token(lease)
        document = {
            "action": outcome.action.value,
            "reason_code": outcome.reason_code,
            "retryable": outcome.retryable,
            "mutation_outcome": (
                outcome.mutation.outcome.value
                if outcome.mutation is not None
                else None
            ),
            "mutation_reason_code": (
                outcome.mutation.reason_code.value
                if outcome.mutation is not None
                else None
            ),
        }
        if (
            not _safe_ref(outcome.candidate_ref)
            or not _safe_reason(outcome.reason_code)
            or not isinstance(outcome.retryable, bool)
        ):
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_outcome_invalid"
            )
        candidate_digest = _plain_digest(outcome.candidate_ref)
        outcome_digest = _plain_digest(
            json.dumps(
                document, sort_keys=True, separators=(",", ":")
            )
        )
        row_id = "sfu-rec-out-" + _plain_digest(
            "\0".join(
                (
                    lease.scope.tenant_ref,
                    lease.scope.room_ref,
                    candidate_digest,
                    str(token),
                )
            )
        )[:32]
        try:
            with Session(self._engine) as db:
                control = _find_reconciliation_control(db, lease.scope)
                if (
                    control is None
                    or control.owner_digest
                    != self._owner_digest(lease.owner_ref)
                    or control.fencing_token != token
                    or control.lease_expires_at_ms <= now_ms
                ):
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_lease_stale"
                    )
                existing = db.get(SfuFanoutReconciliationOutcomeDB, row_id)
                if existing is not None:
                    if existing.outcome_digest == outcome_digest:
                        return
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_outcome_conflict"
                    )
                _purge_reconciliation_outcomes(
                    db,
                    tenant_id=lease.scope.tenant_ref,
                    room_id=lease.scope.room_ref,
                    now_ms=now_ms,
                    limit=self._purge_batch,
                )
                active_count = len(
                    db.exec(
                        select(SfuFanoutReconciliationOutcomeDB.id)
                        .where(
                            SfuFanoutReconciliationOutcomeDB.tenant_id
                            == lease.scope.tenant_ref,
                            SfuFanoutReconciliationOutcomeDB.room_id
                            == lease.scope.room_ref,
                        )
                        .limit(self._outcome_capacity)
                    ).all()
                )
                if active_count >= self._outcome_capacity:
                    raise SfuHubControlRepositoryError(
                        "sfu_reconciliation_outcome_capacity"
                    )
                db.add(
                    SfuFanoutReconciliationOutcomeDB(
                        id=row_id,
                        tenant_id=lease.scope.tenant_ref,
                        room_id=lease.scope.room_ref,
                        candidate_digest=candidate_digest,
                        outcome_digest=outcome_digest,
                        action=document["action"],
                        reason_code=outcome.reason_code,
                        retryable=outcome.retryable,
                        mutation_outcome=document["mutation_outcome"],
                        mutation_reason_code=document[
                            "mutation_reason_code"
                        ],
                        fencing_token=token,
                        retain_until_ms=now_ms
                        + self._outcome_retention,
                        created_at=now_ms / 1000.0,
                    )
                )
                db.commit()
        except SfuHubControlRepositoryError:
            raise
        except IntegrityError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_outcome_conflict"
            ) from exc
        except SQLAlchemyError as exc:
            raise SfuHubControlRepositoryError(
                "sfu_reconciliation_store_unavailable"
            ) from exc

    def _owner_digest(self, owner_ref: str) -> str:
        return hmac.new(
            self._secret,
            b"ananta:sfu-reconciliation-owner:v1\0"
            + owner_ref.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


def _validate_reconciliation_scope(
    scope: RouteReconciliationScope, owner_ref: str
) -> None:
    if not all(
        _safe_ref(value)
        for value in (scope.tenant_ref, scope.room_ref, owner_ref)
    ):
        raise SfuHubControlRepositoryError(
            "sfu_reconciliation_scope_invalid"
        )


def _find_reconciliation_control(
    db: Session, scope: RouteReconciliationScope
) -> SfuFanoutReconciliationControlDB | None:
    return db.exec(
        select(SfuFanoutReconciliationControlDB).where(
            SfuFanoutReconciliationControlDB.tenant_id
            == scope.tenant_ref,
            SfuFanoutReconciliationControlDB.room_id == scope.room_ref,
        )
    ).first()


def _reconciliation_control_id(scope: RouteReconciliationScope) -> str:
    return "sfu-rec-" + _plain_digest(
        scope.tenant_ref + "\0" + scope.room_ref
    )[:32]


def _lease_token(lease: RouteReconciliationLease) -> int:
    if (
        not isinstance(lease.fencing_token, str)
        or not lease.fencing_token.isdecimal()
    ):
        raise SfuHubControlRepositoryError(
            "sfu_reconciliation_lease_invalid"
        )
    value = int(lease.fencing_token)
    if value <= 0:
        raise SfuHubControlRepositoryError(
            "sfu_reconciliation_lease_invalid"
        )
    return value


def _purge_reconciliation_outcomes(
    db: Session,
    *,
    tenant_id: str,
    room_id: str,
    now_ms: int,
    limit: int,
) -> int:
    ids = db.exec(
        select(SfuFanoutReconciliationOutcomeDB.id)
        .where(
            SfuFanoutReconciliationOutcomeDB.tenant_id == tenant_id,
            SfuFanoutReconciliationOutcomeDB.room_id == room_id,
            SfuFanoutReconciliationOutcomeDB.retain_until_ms <= now_ms,
        )
        .order_by(SfuFanoutReconciliationOutcomeDB.retain_until_ms)
        .limit(limit)
    ).all()
    if ids:
        db.exec(
            sa.delete(SfuFanoutReconciliationOutcomeDB).where(
                SfuFanoutReconciliationOutcomeDB.id.in_(ids)
            )
        )
        db.flush()
    return len(ids)


__all__ = [
    "SqlSfuFanoutReconciliationControlRepository",
]
