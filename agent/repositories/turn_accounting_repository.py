"""Atomic SQL persistence for pseudonymized TURN accounting."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import fields

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models.turn_accounting import (
    TurnAccountingLedgerDB,
    TurnAccountingSourceCursorDB,
)
from agent.services.turn_accounting_repository_port import (
    TurnAccountingCounters,
    TurnAccountingIngestRequest,
    TurnAccountingPage,
    TurnAccountingRecord,
    TurnAccountingRepositoryError,
    TurnAccountingRepositoryResult,
    TurnAccountingScope,
)


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PSEUDONYM = re.compile(r"^[0-9a-f]{24}$")
_RECEIVER_CLASSES = frozenset(
    {"direct_capable", "relay_required", "relay_preferred", "unknown"}
)
_COUNTER_NAMES = tuple(field.name for field in fields(TurnAccountingCounters))


class SqlTurnAccountingRepository:
    """One transaction owns replay receipt, sequence cursor and ledger append."""

    def __init__(self, *, db_engine=default_engine, purge_batch: int = 200) -> None:
        if not 1 <= purge_batch <= 1000:
            raise ValueError("turn_accounting_purge_batch_invalid")
        self._engine = db_engine
        self._purge_batch = purge_batch

    def ingest(self, request: TurnAccountingIngestRequest) -> TurnAccountingRepositoryResult:
        _validate_request(request)
        for attempt in range(3):
            try:
                with Session(self._engine) as db:
                    self._purge_rows(db, now=request.now, limit=self._purge_batch)
                    existing = db.get(TurnAccountingLedgerDB, request.event_digest)
                    if existing is not None:
                        if existing.request_digest != request.request_digest:
                            raise TurnAccountingRepositoryError(
                                "turn_accounting_event_conflict", 409
                            )
                        return TurnAccountingRepositoryResult("replayed", _record(existing))

                    cursor = db.exec(
                        select(TurnAccountingSourceCursorDB)
                        .where(TurnAccountingSourceCursorDB.id == request.source_pseudonym)
                        .with_for_update()
                    ).first()
                    if cursor is not None and cursor.retained_until <= request.now:
                        db.delete(cursor)
                        db.flush()
                        cursor = None
                    self._enforce_capacity(db, request, new_source=cursor is None)
                    counters, reasons = _classify(request, cursor)
                    row = _ledger_row(request, counters, reasons)
                    db.add(row)
                    if cursor is None:
                        db.add(_cursor_row(request))
                    else:
                        updated = db.exec(
                            sa.update(TurnAccountingSourceCursorDB)
                            .where(
                                TurnAccountingSourceCursorDB.id == cursor.id,
                                TurnAccountingSourceCursorDB.version == cursor.version,
                            )
                            .values(**_cursor_values(request, version=cursor.version + 1))
                        )
                        if int(updated.rowcount or 0) != 1:
                            db.rollback()
                            continue
                    db.commit()
                    return TurnAccountingRepositoryResult("accepted", _record(row))
            except TurnAccountingRepositoryError:
                raise
            except IntegrityError as exc:
                if attempt < 2:
                    continue
                raise TurnAccountingRepositoryError(
                    "turn_accounting_concurrent_write_conflict", 409
                ) from exc
            except SQLAlchemyError as exc:
                raise TurnAccountingRepositoryError(
                    "turn_accounting_store_unavailable", 503
                ) from exc
        raise TurnAccountingRepositoryError("turn_accounting_concurrent_write_conflict", 409)

    def page(
        self,
        scope: TurnAccountingScope,
        *,
        cursor: str | None,
        limit: int,
        now: int,
    ) -> TurnAccountingPage:
        _validate_scope(scope)
        if isinstance(now, bool) or not isinstance(now, int) or now < 0 or not 1 <= limit <= 200:
            raise TurnAccountingRepositoryError("turn_accounting_page_invalid", 400)
        after = _decode_cursor(cursor) if cursor is not None else None
        try:
            with Session(self._engine) as db:
                statement = select(TurnAccountingLedgerDB).where(
                    TurnAccountingLedgerDB.tenant_pseudonym == scope.tenant_pseudonym,
                    TurnAccountingLedgerDB.pool_pseudonym == scope.pool_pseudonym,
                    TurnAccountingLedgerDB.retained_until > now,
                )
                if after is not None:
                    window, event_id = after
                    statement = statement.where(
                        sa.or_(
                            TurnAccountingLedgerDB.window_started_at_seconds > window,
                            sa.and_(
                                TurnAccountingLedgerDB.window_started_at_seconds == window,
                                TurnAccountingLedgerDB.id > event_id,
                            ),
                        )
                    )
                rows = db.exec(
                    statement.order_by(
                        TurnAccountingLedgerDB.window_started_at_seconds,
                        TurnAccountingLedgerDB.id,
                    ).limit(limit + 1)
                ).all()
                selected = rows[:limit]
                next_cursor = (
                    _encode_cursor(
                        selected[-1].window_started_at_seconds,
                        selected[-1].id,
                    )
                    if len(rows) > limit and selected
                    else None
                )
                return TurnAccountingPage(tuple(_record(row) for row in selected), next_cursor)
        except TurnAccountingRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise TurnAccountingRepositoryError("turn_accounting_store_unavailable", 503) from exc

    def purge_expired(self, *, now: int, limit: int) -> int:
        if isinstance(now, bool) or not isinstance(now, int) or now < 0 or not 1 <= limit <= 1000:
            raise TurnAccountingRepositoryError("turn_accounting_purge_invalid", 400)
        try:
            with Session(self._engine) as db:
                purged = self._purge_rows(db, now=now, limit=limit)
                db.commit()
                return purged
        except SQLAlchemyError as exc:
            raise TurnAccountingRepositoryError("turn_accounting_store_unavailable", 503) from exc

    def _enforce_capacity(
        self,
        db: Session,
        request: TurnAccountingIngestRequest,
        *,
        new_source: bool,
    ) -> None:
        scope_filter = (
            TurnAccountingLedgerDB.tenant_pseudonym == request.tenant_pseudonym,
            TurnAccountingLedgerDB.pool_pseudonym == request.pool_pseudonym,
            TurnAccountingLedgerDB.retained_until > request.now,
        )
        record_count = int(
            db.exec(select(sa.func.count(TurnAccountingLedgerDB.id)).where(*scope_filter)).one()
        )
        if record_count >= request.record_capacity_max:
            raise TurnAccountingRepositoryError(
                "turn_accounting_repository_capacity_exceeded", 503
            )
        if not new_source:
            return
        source_count = int(
            db.exec(
                select(sa.func.count(TurnAccountingSourceCursorDB.id)).where(
                    TurnAccountingSourceCursorDB.tenant_pseudonym == request.tenant_pseudonym,
                    TurnAccountingSourceCursorDB.pool_pseudonym == request.pool_pseudonym,
                    TurnAccountingSourceCursorDB.retained_until > request.now,
                )
            ).one()
        )
        if source_count >= request.source_capacity_max:
            raise TurnAccountingRepositoryError("turn_accounting_source_capacity_exceeded", 503)

    @staticmethod
    def _purge_rows(db: Session, *, now: int, limit: int) -> int:
        ledger_ids = db.exec(
            select(TurnAccountingLedgerDB.id)
            .where(TurnAccountingLedgerDB.retained_until <= now)
            .order_by(TurnAccountingLedgerDB.retained_until, TurnAccountingLedgerDB.id)
            .limit(limit)
        ).all()
        if ledger_ids:
            db.exec(sa.delete(TurnAccountingLedgerDB).where(TurnAccountingLedgerDB.id.in_(ledger_ids)))
        remaining = limit - len(ledger_ids)
        cursor_ids = (
            db.exec(
                select(TurnAccountingSourceCursorDB.id)
                .where(TurnAccountingSourceCursorDB.retained_until <= now)
                .order_by(
                    TurnAccountingSourceCursorDB.retained_until,
                    TurnAccountingSourceCursorDB.id,
                )
                .limit(remaining)
            ).all()
            if remaining
            else []
        )
        if cursor_ids:
            db.exec(
                sa.delete(TurnAccountingSourceCursorDB).where(
                    TurnAccountingSourceCursorDB.id.in_(cursor_ids)
                )
            )
        return len(ledger_ids) + len(cursor_ids)


def _classify(
    request: TurnAccountingIngestRequest,
    cursor: TurnAccountingSourceCursorDB | None,
) -> tuple[TurnAccountingCounters, tuple[str, ...]]:
    reasons: list[str] = []
    counters = request.counters
    if cursor is not None:
        if (
            cursor.tenant_pseudonym != request.tenant_pseudonym
            or cursor.pool_pseudonym != request.pool_pseudonym
            or cursor.allocation_pseudonym != request.allocation_pseudonym
            or cursor.node_pseudonym != request.node_pseudonym
        ):
            raise TurnAccountingRepositoryError("turn_accounting_source_scope_conflict", 409)
        if cursor.runtime_epoch_pseudonym != request.runtime_epoch_pseudonym:
            reasons.append("turn_accounting_runtime_restart_estimated")
        elif request.sequence <= cursor.highest_sequence:
            raise TurnAccountingRepositoryError("turn_accounting_sequence_stale", 409)
        else:
            if request.sequence > cursor.highest_sequence + 1:
                reasons.append("turn_accounting_sequence_gap_estimated")
            if request.window_started_at_seconds == cursor.window_started_at_seconds:
                counters, regressed = request.counters.delta(_cursor_counters(cursor))
                if regressed:
                    reasons.append("turn_accounting_counter_regression_estimated")
    if request.late:
        reasons.append("turn_accounting_late_window")
    if not reasons:
        reasons.append("turn_accounting_accepted")
    return counters, tuple(reasons)


def _ledger_row(
    request: TurnAccountingIngestRequest,
    counters: TurnAccountingCounters,
    reasons: tuple[str, ...],
) -> TurnAccountingLedgerDB:
    return TurnAccountingLedgerDB(
        id=request.event_digest,
        request_digest=request.request_digest,
        source_pseudonym=request.source_pseudonym,
        runtime_epoch_pseudonym=request.runtime_epoch_pseudonym,
        credential_pseudonym=request.credential_pseudonym,
        tenant_pseudonym=request.tenant_pseudonym,
        pool_pseudonym=request.pool_pseudonym,
        room_pseudonym=request.room_pseudonym,
        allocation_pseudonym=request.allocation_pseudonym,
        node_pseudonym=request.node_pseudonym,
        receiver_class=request.receiver_class,
        sequence=request.sequence,
        observed_at_seconds=request.observed_at_seconds,
        window_started_at_seconds=request.window_started_at_seconds,
        **counters.values(),
        reason_codes=list(reasons),
        retained_until=request.retained_until,
        created_at=float(request.now),
    )


def _cursor_row(request: TurnAccountingIngestRequest) -> TurnAccountingSourceCursorDB:
    return TurnAccountingSourceCursorDB(
        id=request.source_pseudonym,
        tenant_pseudonym=request.tenant_pseudonym,
        pool_pseudonym=request.pool_pseudonym,
        allocation_pseudonym=request.allocation_pseudonym,
        node_pseudonym=request.node_pseudonym,
        **_cursor_values(request, version=1),
        created_at=float(request.now),
    )


def _cursor_values(request: TurnAccountingIngestRequest, *, version: int) -> dict[str, object]:
    return {
        "runtime_epoch_pseudonym": request.runtime_epoch_pseudonym,
        "highest_sequence": request.sequence,
        "window_started_at_seconds": request.window_started_at_seconds,
        **request.counters.values(),
        "version": version,
        "retained_until": request.retained_until,
        "updated_at": float(request.now),
    }


def _cursor_counters(cursor: TurnAccountingSourceCursorDB) -> TurnAccountingCounters:
    return TurnAccountingCounters(**{name: getattr(cursor, name) for name in _COUNTER_NAMES})


def _record(row: TurnAccountingLedgerDB) -> TurnAccountingRecord:
    return TurnAccountingRecord(
        sequence=row.sequence,
        observed_at_seconds=row.observed_at_seconds,
        window_started_at_seconds=row.window_started_at_seconds,
        scope_pseudonyms={
            "credential": row.credential_pseudonym,
            "tenant": row.tenant_pseudonym,
            "pool": row.pool_pseudonym,
            "room": row.room_pseudonym,
            "allocation": row.allocation_pseudonym,
            "node": row.node_pseudonym,
        },
        receiver_class=row.receiver_class,
        counters=TurnAccountingCounters(
            **{name: getattr(row, name) for name in _COUNTER_NAMES}
        ),
        reason_codes=tuple(row.reason_codes),
    )


def _validate_request(request: TurnAccountingIngestRequest) -> None:
    if not isinstance(request, TurnAccountingIngestRequest):
        raise TurnAccountingRepositoryError("turn_accounting_request_invalid", 400)
    for digest in (request.event_digest, request.request_digest, request.source_pseudonym):
        if not _DIGEST.fullmatch(digest):
            raise TurnAccountingRepositoryError("turn_accounting_digest_invalid", 400)
    for pseudonym in (
        request.runtime_epoch_pseudonym,
        request.credential_pseudonym,
        request.tenant_pseudonym,
        request.pool_pseudonym,
        request.room_pseudonym,
        request.allocation_pseudonym,
        request.node_pseudonym,
    ):
        if not _PSEUDONYM.fullmatch(pseudonym):
            raise TurnAccountingRepositoryError("turn_accounting_pseudonym_invalid", 400)
    if request.receiver_class not in _RECEIVER_CLASSES:
        raise TurnAccountingRepositoryError("turn_accounting_receiver_class_invalid", 400)
    integers = (
        request.sequence,
        request.observed_at_seconds,
        request.window_started_at_seconds,
        request.retained_until,
        request.source_capacity_max,
        request.record_capacity_max,
        request.now,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in integers):
        raise TurnAccountingRepositoryError("turn_accounting_request_invalid", 400)
    if (
        request.sequence < 1
        or request.retained_until <= request.now
        or not 1 <= request.source_capacity_max <= 100_000
        or not request.source_capacity_max <= request.record_capacity_max <= 10_000_000
        or not isinstance(request.late, bool)
    ):
        raise TurnAccountingRepositoryError("turn_accounting_request_invalid", 400)


def _validate_scope(scope: TurnAccountingScope) -> None:
    if (
        not isinstance(scope, TurnAccountingScope)
        or not _PSEUDONYM.fullmatch(scope.tenant_pseudonym)
        or not _PSEUDONYM.fullmatch(scope.pool_pseudonym)
    ):
        raise TurnAccountingRepositoryError("turn_accounting_scope_invalid", 400)


def _encode_cursor(window: int, event_id: str) -> str:
    raw = json.dumps([window, event_id], separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 256:
            raise ValueError
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw.decode("ascii"))
        if (
            not isinstance(value, list)
            or len(value) != 2
            or isinstance(value[0], bool)
            or not isinstance(value[0], int)
            or value[0] < 0
            or not isinstance(value[1], str)
            or not _DIGEST.fullmatch(value[1])
        ):
            raise ValueError
        return value[0], value[1]
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TurnAccountingRepositoryError("turn_accounting_cursor_invalid", 400) from exc


__all__ = ["SqlTurnAccountingRepository"]
