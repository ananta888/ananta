"""Cross-Hub room speech leases, not a second task or inference queue."""

from contextlib import contextmanager

from sqlalchemy import JSON, BigInteger, Column, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from agent.models.meet_speaker_floor import AGING_MS, CLEANUP_MS, OUTPUT_MS, WAIT_MS, require_clock
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_speaker_floor import validate_speaker_permit

_metadata = MetaData()
rooms = Table(
    "meet_speaker_floor_rooms",
    _metadata,
    Column("id", String(64), primary_key=True),
    Column("sequence", BigInteger, nullable=False),
)
turns = Table(
    "meet_speaker_floor_turns",
    _metadata,
    Column("id", String(64), primary_key=True),
    Column("room_id", String(64), nullable=False, index=True),
    Column("sequence", BigInteger, nullable=False),
    Column("binding", JSON, nullable=False),
    Column("state", String(16), nullable=False),
    Column("queued_ms", BigInteger, nullable=False),
    Column("expires_ms", BigInteger, nullable=False),
    Column("quiet_ms", BigInteger, nullable=False),
)


def _permit(row):
    return {"id": row["id"], "sequence": row["sequence"], "expires_ms": row["expires_ms"]}


class SqlMeetSpeakerFloor:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    @contextmanager
    def _locked(self, turn, now_ms):
        require_clock(now_ms)
        # A separate transaction handles first-use races without aborting the
        # PostgreSQL transaction that subsequently owns the room row lock.
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(rooms).values(id=turn.room_key, sequence=0))
        except IntegrityError:
            pass
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(rooms).where(rooms.c.id == turn.room_key).values(sequence=rooms.c.sequence)
            )
            if changed.rowcount != 1:
                raise MeetError("meet_speaker_room_unavailable", 503)
            self._expire(connection, turn.room_key, now_ms)
            yield connection

    def _expire(self, connection, room, now_ms):
        scope = turns.c.room_id == room
        connection.execute(
            update(turns).where(scope, turns.c.state == "queued", turns.c.expires_ms <= now_ms).values(state="expired")
        )
        connection.execute(
            update(turns)
            .where(scope, turns.c.state == "active", turns.c.expires_ms <= now_ms)
            .values(state="quarantine", quiet_ms=turns.c.expires_ms + CLEANUP_MS)
        )
        connection.execute(
            update(turns)
            .where(scope, turns.c.state == "quarantine", turns.c.quiet_ms <= now_ms)
            .values(state="finished")
        )

    def _row(self, connection, turn):
        row = (
            connection.execute(select(turns).where(turns.c.room_id == turn.room_key, turns.c.id == turn.identity))
            .mappings()
            .first()
        )
        if row is None or row["binding"] != turn.metadata:
            raise MeetError("meet_speaker_turn_changed", 409)
        return row

    def reserve(self, turn, now_ms):
        with self._locked(turn, now_ms) as connection:
            if turn.deadline_ms <= now_ms:
                raise MeetError("meet_speaker_turn_expired", 409)
            if connection.execute(select(turns.c.id).where(turns.c.id == turn.identity)).first():
                raise MeetError("meet_speaker_duplicate_turn", 409)
            waiting = connection.execute(
                select(turns.c.id).where(turns.c.room_id == turn.room_key, turns.c.state == "queued").limit(4)
            ).all()
            if len(waiting) >= 4:
                raise MeetError("meet_speaker_waiters_full", 429)
            sequence = connection.execute(select(rooms.c.sequence).where(rooms.c.id == turn.room_key)).scalar_one() + 1
            if sequence >= 2**53:
                raise MeetError("meet_speaker_sequence_exhausted", 503)
            connection.execute(update(rooms).where(rooms.c.id == turn.room_key).values(sequence=sequence))
            connection.execute(
                insert(turns).values(
                    id=turn.identity,
                    room_id=turn.room_key,
                    sequence=sequence,
                    binding=turn.metadata,
                    state="queued",
                    queued_ms=now_ms,
                    expires_ms=min(turn.deadline_ms, now_ms + WAIT_MS),
                    quiet_ms=0,
                )
            )

    def poll(self, turn, now_ms):
        with self._locked(turn, now_ms) as connection:
            row = self._row(connection, turn)
            if row["state"] == "active":
                return _permit(row)  # Polling never extends an existing permit.
            if row["state"] != "queued":
                raise MeetError("meet_speaker_turn_inactive", 409)
            busy = connection.execute(
                select(turns.c.id)
                .where(turns.c.room_id == turn.room_key, turns.c.state.in_(("active", "quarantine")))
                .limit(1)
            ).first()
            if busy is not None:
                return None
            waiting = (
                connection.execute(select(turns).where(turns.c.room_id == turn.room_key, turns.c.state == "queued"))
                .mappings()
                .all()
            )
            # Bounded at four rows. Aging reaches maximum priority in six seconds;
            # equal effective priority is strict arrival FIFO, never caller order.
            first = min(
                waiting,
                key=lambda r: (
                    -min(2, r["binding"]["priority"] + max(0, now_ms - r["queued_ms"]) // AGING_MS),
                    r["sequence"],
                ),
            )
            if first["id"] != turn.identity:
                return None
            expires = min(turn.deadline_ms, now_ms + OUTPUT_MS)
            connection.execute(
                update(turns).where(turns.c.id == turn.identity).values(state="active", expires_ms=expires)
            )
            return _permit(dict(row) | {"expires_ms": expires})

    def current(self, turn, permit, now_ms):
        with self._locked(turn, now_ms) as connection:
            row = self._row(connection, turn)
            return (
                row["state"] == "active"
                and type(permit) is dict
                and set(permit) == {"id", "sequence", "expires_ms"}
                and type(permit["sequence"]) is int
                and type(permit["expires_ms"]) is int
                and permit == _permit(row)
            )

    def cancel(self, turn, now_ms):
        """Exact Hub withdrawal/completion; a callback is not proof of immediate silence."""
        with self._locked(turn, now_ms) as connection:
            row = self._row(connection, turn)
            if row["state"] == "queued":
                values = {"state": "finished"}
            elif row["state"] == "active":
                values = {"state": "quarantine", "quiet_ms": min(now_ms, row["expires_ms"]) + CLEANUP_MS}
            else:
                return  # Repeated/delayed callbacks cannot extend or undo quarantine.
            connection.execute(update(turns).where(turns.c.id == turn.identity).values(**values))

    def _owned(self, connection, owner):
        # At most four queued plus one active/quarantined row. Never expose a
        # different tenant/task's speaker identity in an owner's control reply.
        candidates = (
            connection.execute(
                select(turns).where(turns.c.room_id == owner.room_key, turns.c.state.in_(("queued", "active")))
            )
            .mappings()
            .all()
        )
        return [row for row in candidates if all(row["binding"].get(k) == v for k, v in owner.metadata.items())]

    def projection(self, owner, now_ms):
        with self._locked(owner, now_ms) as connection:
            return next((_permit(row) for row in self._owned(connection, owner) if row["state"] == "active"), None)

    def complete(self, owner, permit, now_ms):
        permit = validate_speaker_permit(permit)
        with self._locked(owner, now_ms) as connection:
            for row in self._owned(connection, owner):
                if row["state"] == "active" and _permit(row) == permit:
                    connection.execute(
                        update(turns)
                        .where(turns.c.id == row["id"])
                        .values(state="quarantine", quiet_ms=now_ms + CLEANUP_MS)
                    )
                    return True
            return False  # An old/foreign completion cannot stop a newer turn.

    def revoke(self, owner, now_ms):
        with self._locked(owner, now_ms) as connection:
            for row in self._owned(connection, owner):
                values = (
                    {"state": "quarantine", "quiet_ms": now_ms + CLEANUP_MS}
                    if row["state"] == "active"
                    else {"state": "finished"}
                )
                connection.execute(update(turns).where(turns.c.id == row["id"]).values(**values))

    def preempt(self, turn, now_ms):
        """Apply an explicit Hub decision only against a lower-priority same-org turn."""
        with self._locked(turn, now_ms) as connection:
            waiting = self._row(connection, turn)
            if waiting["state"] != "queued" or turn.priority == 0 or not turn.organization_id:
                return False
            active = (
                connection.execute(select(turns).where(turns.c.room_id == turn.room_key, turns.c.state == "active"))
                .mappings()
                .first()
            )
            if (
                active is None
                or active["binding"]["priority"] >= turn.priority
                or any(
                    active["binding"].get(k) != turn.metadata[k] for k in ("tenant_id", "project_id", "organization_id")
                )
            ):
                return False
            connection.execute(
                update(turns).where(turns.c.id == active["id"]).values(state="quarantine", quiet_ms=now_ms + CLEANUP_MS)
            )
            return True
