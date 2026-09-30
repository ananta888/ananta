"""SQL idempotency ledger for SFU broadcast commands."""

from __future__ import annotations

import hashlib
import time
from typing import Callable

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.sfu_hub_control import SfuCommandIdempotencyLedgerDB
from agent.models.sfu_broadcast_command import (
    SfuBroadcastCommandError,
    SfuBroadcastCommandResult,
)
from agent.repositories.sfu_hub_control_validation import (
    _DIGEST,
    _finite_time,
    _plain_digest,
)


class SqlSfuBroadcastCommandLedger:
    """Restart-stable command idempotency without request payload storage."""

    def __init__(
        self,
        *,
        db_engine=default_engine,
        max_entries: int = 4_096,
        retention_seconds: int = 3_600,
        purge_batch: int = 128,
        delivery_retry_seconds: int = 5,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (
            not 1 <= max_entries <= 1_000_000
            or not 60 <= retention_seconds <= 86_400
            or not 1 <= purge_batch <= 1_000
            or not 1 <= delivery_retry_seconds < retention_seconds
        ):
            raise ValueError("sfu_command_ledger_limits_invalid")
        self._engine = db_engine
        self._max_entries = max_entries
        self._retention = retention_seconds
        self._purge_batch = purge_batch
        self._delivery_retry = delivery_retry_seconds
        self._clock = clock

    def claim(
        self,
        scope_digest: str,
        key_digest: str,
        request_digest: str,
        now: float,
    ) -> tuple[str, SfuBroadcastCommandResult | None]:
        _validate_ledger_digests(scope_digest, key_digest, request_digest)
        timestamp = _finite_time(now, "sfu_command_ledger_clock_invalid")
        try:
            with Session(self._engine) as db:
                _purge_ledger(db, timestamp, self._purge_batch)
                current = _find_ledger(db, scope_digest, key_digest)
                if current is not None and current.expires_at > timestamp:
                    result = self._claim_existing(db, current, request_digest, timestamp)
                    db.commit()
                    return result
                if current is not None:
                    db.delete(current)
                    db.flush()
                capacity = len(
                    db.exec(
                        select(SfuCommandIdempotencyLedgerDB.id)
                        .order_by(
                            SfuCommandIdempotencyLedgerDB.created_at
                        )
                        .limit(self._max_entries)
                    ).all()
                )
                if capacity >= self._max_entries:
                    db.rollback()
                    return "capacity", None
                db.add(
                    SfuCommandIdempotencyLedgerDB(
                        id=_ledger_id(scope_digest, key_digest),
                        scope_digest=scope_digest,
                        key_digest=key_digest,
                        request_digest=request_digest,
                        status="pending",
                        operation_id=_ledger_operation_id(scope_digest, key_digest, request_digest),
                        delivery_state="delivering",
                        delivery_attempts=1,
                        version=1,
                        expires_at=timestamp + self._retention,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                db.commit()
                return "claimed", None
        except IntegrityError:
            with Session(self._engine) as db:
                current = _find_ledger(db, scope_digest, key_digest)
                if current is None:
                    return "capacity", None
                result = self._claim_existing(db, current, request_digest, timestamp)
                db.commit()
                return result
        except SQLAlchemyError as exc:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_store_unavailable", 503
            ) from exc

    def complete(
        self,
        scope_digest: str,
        key_digest: str,
        request_digest: str,
        result: SfuBroadcastCommandResult,
    ) -> None:
        _validate_ledger_digests(scope_digest, key_digest, request_digest)
        try:
            with Session(self._engine) as db:
                current = _find_ledger(db, scope_digest, key_digest)
                if (
                    current is None
                    or current.request_digest != request_digest
                ):
                    raise SfuBroadcastCommandError(
                        "sfu_command_idempotency_state_invalid", 503
                    )
                if current.status == "completed":
                    if _ledger_result(current) == result:
                        return
                    raise SfuBroadcastCommandError(
                        "sfu_command_idempotency_state_invalid", 503
                    )
                updated = db.exec(
                    sa.update(SfuCommandIdempotencyLedgerDB)
                    .where(
                        SfuCommandIdempotencyLedgerDB.id == current.id,
                        SfuCommandIdempotencyLedgerDB.version
                        == current.version,
                        SfuCommandIdempotencyLedgerDB.status == "pending",
                    )
                    .values(
                        status="completed",
                        delivery_state="completed",
                        result_accepted=result.accepted,
                        result_effective_version=result.effective_version,
                        result_state=result.state,
                        result_reason_code=result.reason_code,
                        result_code=result.reason_code,
                        result_version=result.effective_version,
                        result_command_ref=result.command_ref,
                        version=current.version + 1,
                        updated_at=_finite_time(
                            self._clock(), "sfu_command_ledger_clock_invalid"
                        ),
                    )
                )
                if int(updated.rowcount or 0) != 1:
                    db.rollback()
                    raise SfuBroadcastCommandError(
                        "sfu_command_idempotency_state_invalid", 503
                    )
                db.commit()
        except SfuBroadcastCommandError:
            raise
        except SQLAlchemyError as exc:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_store_unavailable", 503
            ) from exc

    def abort(
        self, scope_digest: str, key_digest: str, request_digest: str
    ) -> None:
        _validate_ledger_digests(scope_digest, key_digest, request_digest)
        try:
            with Session(self._engine) as db:
                db.exec(
                    sa.delete(SfuCommandIdempotencyLedgerDB).where(
                        SfuCommandIdempotencyLedgerDB.scope_digest
                        == scope_digest,
                        SfuCommandIdempotencyLedgerDB.key_digest == key_digest,
                        SfuCommandIdempotencyLedgerDB.request_digest
                        == request_digest,
                        SfuCommandIdempotencyLedgerDB.status == "pending",
                    )
                )
                db.commit()
        except SQLAlchemyError as exc:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_store_unavailable", 503
            ) from exc

    def _claim_existing(
        self,
        db: Session,
        current: SfuCommandIdempotencyLedgerDB,
        request_digest: str,
        timestamp: float,
    ) -> tuple[str, SfuBroadcastCommandResult | None]:
        if current.request_digest != request_digest:
            return "conflict", None
        if current.status == "completed":
            result = _ledger_result(current)
            return ("replay", result) if result is not None else ("conflict", None)
        if (
            current.delivery_state == "delivering"
            and current.updated_at + self._delivery_retry > timestamp
        ):
            return "in_progress", None
        updated = db.exec(
            sa.update(SfuCommandIdempotencyLedgerDB)
            .where(
                SfuCommandIdempotencyLedgerDB.id == current.id,
                SfuCommandIdempotencyLedgerDB.version == current.version,
                SfuCommandIdempotencyLedgerDB.status == "pending",
            )
            .values(
                operation_id=current.operation_id
                or _ledger_operation_id(
                    current.scope_digest,
                    current.key_digest,
                    current.request_digest,
                ),
                delivery_state="delivering",
                delivery_attempts=current.delivery_attempts + 1,
                version=current.version + 1,
                updated_at=timestamp,
            )
        )
        if int(updated.rowcount or 0) != 1:
            return "in_progress", None
        return "claimed", None

    def purge(self, *, now: float, limit: int | None = None) -> int:
        timestamp = _finite_time(now, "sfu_command_ledger_clock_invalid")
        bounded = self._purge_batch if limit is None else limit
        if not 1 <= bounded <= 1_000:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_purge_limit_invalid", 503
            )
        try:
            with Session(self._engine) as db:
                count = _purge_ledger(db, timestamp, bounded)
                db.commit()
                return count
        except SQLAlchemyError as exc:
            raise SfuBroadcastCommandError(
                "sfu_command_idempotency_store_unavailable", 503
            ) from exc


def _validate_ledger_digests(*values: str) -> None:
    if any(
        not isinstance(value, str) or not _DIGEST.fullmatch(value)
        for value in values
    ):
        raise SfuBroadcastCommandError(
            "sfu_command_idempotency_digest_invalid", 503
        )


def _ledger_id(scope_digest: str, key_digest: str) -> str:
    return "sfu-cmd-" + _plain_digest(
        scope_digest + "\0" + key_digest
    )[:32]


def _find_ledger(
    db: Session, scope_digest: str, key_digest: str
) -> SfuCommandIdempotencyLedgerDB | None:
    return db.exec(
        select(SfuCommandIdempotencyLedgerDB).where(
            SfuCommandIdempotencyLedgerDB.scope_digest == scope_digest,
            SfuCommandIdempotencyLedgerDB.key_digest == key_digest,
        )
    ).first()


def _ledger_operation_id(
    scope_digest: str, key_digest: str, request_digest: str
) -> str:
    digest = hashlib.sha256(
        f"{scope_digest}\0{key_digest}\0{request_digest}".encode("ascii")
    ).hexdigest()
    return "sfcop1." + digest[:32]


def _ledger_result(
    row: SfuCommandIdempotencyLedgerDB,
) -> SfuBroadcastCommandResult | None:
    if (
        row.status != "completed"
        or row.result_accepted is None
        or (row.result_version is None and row.result_effective_version is None)
        or row.result_state is None
        or (row.result_code is None and row.result_reason_code is None)
        or row.result_command_ref is None
    ):
        return None
    return SfuBroadcastCommandResult(
        row.result_accepted,
        row.result_version
        if row.result_version is not None
        else row.result_effective_version,
        row.result_state,
        row.result_code if row.result_code is not None else row.result_reason_code,
        row.result_command_ref,
    )


def _purge_ledger(db: Session, now: float, limit: int) -> int:
    ids = db.exec(
        select(SfuCommandIdempotencyLedgerDB.id)
        .where(SfuCommandIdempotencyLedgerDB.expires_at <= now)
        .order_by(SfuCommandIdempotencyLedgerDB.expires_at)
        .limit(limit)
    ).all()
    if ids:
        db.exec(
            sa.delete(SfuCommandIdempotencyLedgerDB).where(
                SfuCommandIdempotencyLedgerDB.id.in_(ids)
            )
        )
        db.flush()
    return len(ids)


__all__ = [
    "SqlSfuBroadcastCommandLedger",
]
