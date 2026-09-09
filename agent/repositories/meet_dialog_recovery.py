"""Durable bounded reconnect resource; no grant issuer or independent task queue."""

from contextlib import contextmanager

from sqlalchemy import JSON, BigInteger, Column, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from agent.models.meet_dialog_recovery import (
    MAX_RECOVERIES,
    RECOVERY_MS,
    RECOVERY_QUIET_MS,
    RecoveryMembership,
    instant,
    validate_recovery_record,
)
from agent.services.meet_contract import MeetError

_metadata = MetaData()
recoveries = Table(
    "meet_dialog_recoveries",
    _metadata,
    Column("task_id", String(160), primary_key=True),
    Column("assignment_digest", String(64), nullable=False),
    Column("deadline_ms", BigInteger, nullable=False),
    Column("membership", JSON, nullable=False),
    Column("retired", JSON, nullable=False),
    Column("attempt", BigInteger, nullable=False),
    Column("state", String(16), nullable=False),
    Column("last_now", BigInteger, nullable=False),
    Column("attempt_until", BigInteger, nullable=False),
    Column("ready_at", BigInteger, nullable=False),
)


def _view(row):
    return {
        "attempt": row["attempt"],
        "state": row["state"],
        "deadline_ms": row["attempt_until"],
        "ready_ms": row["ready_at"],
    }


class SqlDialogRecovery:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    @contextmanager
    def _locked(self, owner, now_ms):
        instant(now_ms)
        failure = None
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(recoveries).where(recoveries.c.task_id == owner.task_id).values(last_now=recoveries.c.last_now)
            )
            if changed.rowcount != 1:
                raise MeetError("meet_recovery_unregistered", 409)
            row = connection.execute(select(recoveries).where(recoveries.c.task_id == owner.task_id)).mappings().one()
            if (row["assignment_digest"], row["deadline_ms"]) != (owner.assignment_digest, owner.deadline_ms):
                raise MeetError("meet_recovery_binding_changed", 409)
            validate_recovery_record(row)
            if (
                row["state"] == "failed"
                or now_ms < row["last_now"]
                or now_ms >= owner.deadline_ms
                or row["state"] != "active"
                and now_ms >= row["attempt_until"]
            ):
                connection.execute(
                    update(recoveries)
                    .where(recoveries.c.task_id == owner.task_id)
                    .values(state="failed", last_now=max(now_ms, row["last_now"]))
                )
                failure = MeetError("meet_recovery_expired", 409)
            else:
                connection.execute(
                    update(recoveries).where(recoveries.c.task_id == owner.task_id).values(last_now=now_ms)
                )
                yield connection, dict(row)
        # Raise after commit: rollback must not resurrect an expired/clock-regressed attempt.
        if failure is not None:
            raise failure

    def _write(self, connection, owner, row, **fields):
        connection.execute(update(recoveries).where(recoveries.c.task_id == owner.task_id).values(**fields))
        return _view(row | fields)

    def observe(self, owner, membership, now_ms):
        instant(now_ms)
        # Never seed an invalid observation. Existing owners still pass through
        # the transaction so deadline/clock failure is terminal and persisted.
        if not now_ms < membership.expires_ms <= owner.deadline_ms:
            with self._locked(owner, now_ms):
                membership.require_current(owner, now_ms)
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(recoveries).values(
                        task_id=owner.task_id,
                        assignment_digest=owner.assignment_digest,
                        deadline_ms=owner.deadline_ms,
                        membership=membership.metadata,
                        retired=[],
                        attempt=0,
                        state="active",
                        last_now=now_ms,
                        attempt_until=0,
                        ready_at=0,
                    )
                )
        except IntegrityError:
            pass
        with self._locked(owner, now_ms) as (connection, row):
            membership.require_current(owner, now_ms)
            previous = RecoveryMembership(**row["membership"])
            if row["state"] == "active":
                membership.require_refresh(previous)
            elif row["state"] == "joining":
                if (
                    membership.session_id == previous.session_id
                    or membership.peer_id == previous.peer_id
                    or membership.epoch <= previous.epoch
                    or membership.generation != 1
                    or any(
                        membership.session_id == old["session_id"] or membership.peer_id == old["peer_id"]
                        for old in row["retired"]
                    )
                ):
                    raise MeetError("meet_recovery_membership_changed", 409)
            else:
                raise MeetError("meet_recovery_not_joining", 409)
            return self._write(
                connection, owner, row, membership=membership.metadata, state="active", attempt_until=0, ready_at=0
            )

    def reserve(self, owner, session_id, now_ms):
        with self._locked(owner, now_ms) as (connection, row):
            if row["state"] != "active" or row["membership"]["session_id"] != session_id:
                raise MeetError("meet_recovery_session_changed", 409)
            if row["attempt"] >= MAX_RECOVERIES or now_ms + RECOVERY_MS > owner.deadline_ms:
                raise MeetError("meet_recovery_budget_exhausted", 409)
            retired = row["retired"] + [{key: row["membership"][key] for key in ("session_id", "peer_id")}]
            return self._write(
                connection,
                owner,
                row,
                attempt=row["attempt"] + 1,
                state="retiring",
                retired=retired,
                attempt_until=now_ms + RECOVERY_MS,
                ready_at=0,
            )

    def _require_attempt(self, row, attempt, session_id):
        if (
            type(attempt) is not int
            or attempt != row["attempt"]
            or attempt < 1
            or row["membership"]["session_id"] != session_id
        ):
            raise MeetError("meet_recovery_attempt_changed", 409)

    def retired(self, owner, attempt, session_id, now_ms):
        with self._locked(owner, now_ms) as (connection, row):
            self._require_attempt(row, attempt, session_id)
            if row["state"] in {"waiting", "joining"}:
                return _view(row)  # Duplicate acknowledgement cannot extend the quarantine.
            if row["state"] != "retiring":
                raise MeetError("meet_recovery_not_retiring", 409)
            return self._write(connection, owner, row, state="waiting", ready_at=now_ms + RECOVERY_QUIET_MS)

    def take_grant(self, owner, attempt, session_id, now_ms):
        with self._locked(owner, now_ms) as (connection, row):
            self._require_attempt(row, attempt, session_id)
            if row["state"] != "waiting" or now_ms < row["ready_at"]:
                raise MeetError("meet_recovery_grant_unavailable", 409)
            return self._write(connection, owner, row, state="joining")

    def pending(self, owner, attempt, session_id, now_ms):
        with self._locked(owner, now_ms) as (_, row):
            self._require_attempt(row, attempt, session_id)
            if row["state"] not in {"retiring", "waiting", "joining"}:
                raise MeetError("meet_recovery_not_pending", 409)
            return _view(row)
