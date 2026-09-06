"""Cross-Hub admission using existing worker-slot leases, not another task queue."""

import hashlib
import json
import re
from contextlib import contextmanager

from sqlalchemy import BigInteger, Column, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from agent.db_models import WorkerSlotLeaseDB
from agent.services.meet_contract import MeetError

_metadata = MetaData()
pools = Table(
    "meet_media_capacity_pools",
    _metadata,
    Column("id", String(128), primary_key=True),
    Column("sequence", BigInteger, nullable=False),
)
slots = WorkerSlotLeaseDB.__table__
_TYPE = "meet_media"


def binding(turn):
    # No prompt, grant, image, transcript, URL, source bytes or model response.
    return {name: turn[name] for name in ("task_id", "lease_id", "tenant_id", "project_id", "deadline")}


class SqlMeetCapacity:
    def __init__(self, engine, pool):
        if not isinstance(pool, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", pool):
            raise ValueError("meet_capacity_pool_invalid")
        self.engine, self.pool = engine, pool

    def initialize(self):
        _metadata.create_all(self.engine)
        slots.create(self.engine, checkfirst=True)
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(pools).values(id=self.pool, sequence=0))
        except IntegrityError:
            pass  # Existing pool is shared, never reset at Hub restart.

    @contextmanager
    def _locked(self):
        # UPDATE takes a database row/write lock on both PostgreSQL and SQLite.
        # A Python lock would not serialize separate Hub processes.
        with self.engine.begin() as connection:
            changed = connection.execute(update(pools).where(pools.c.id == self.pool).values(sequence=pools.c.sequence))
            if changed.rowcount != 1:
                raise MeetError("meet_capacity_pool_unavailable", 503)
            yield connection

    def _scope(self):
        return (slots.c.worker_id == self.pool, slots.c.lease_type == _TYPE)

    def _expire(self, connection, now):
        connection.execute(
            update(slots)
            .where(*self._scope(), slots.c.status.in_(("queued", "active")), slots.c.deadline_at <= now)
            .values(status="stale_released", reason_code="meet_capacity_expired", released_at=now)
        )

    def reserve(self, turn, now):
        metadata = binding(turn)
        identity = hashlib.sha256(json.dumps([self.pool, metadata], sort_keys=True).encode()).hexdigest()
        with self._locked() as connection:
            self._expire(connection, now)
            if connection.execute(select(slots.c.id).where(slots.c.id == identity)).first():
                raise MeetError("meet_capacity_duplicate_dispatch", 409)
            waiting = connection.execute(
                select(slots.c.id).where(*self._scope(), slots.c.status == "queued").limit(4)
            ).all()
            if len(waiting) >= 4:
                raise MeetError("meet_capacity_queue_full", 429)
            sequence = connection.execute(select(pools.c.sequence).where(pools.c.id == self.pool)).scalar_one() + 1
            if sequence >= 2**31:
                raise MeetError("meet_capacity_sequence_exhausted", 503)
            connection.execute(update(pools).where(pools.c.id == self.pool).values(sequence=sequence))
            lease = WorkerSlotLeaseDB(
                id=identity,
                lease_type=_TYPE,
                status="queued",
                worker_id=self.pool,
                worker_kind="meet_media",
                runtime_kind="private_container",
                parent_task_id=turn["task_id"],
                queue_position=sequence,
                acquired_at=now,
                deadline_at=min(turn["deadline"], now + 10),
                reason_code="meet_capacity_waiting",
                lease_metadata=metadata,
            )
            connection.execute(insert(slots).values(**lease.model_dump()))
        return identity

    def poll(self, identity, turn, now):
        with self._locked() as connection:
            self._expire(connection, now)
            current = connection.execute(select(slots).where(*self._scope(), slots.c.id == identity)).mappings().first()
            if (
                current is None
                or current["lease_metadata"] != binding(turn)
                or current["status"] not in {"queued", "active"}
            ):
                raise MeetError("meet_capacity_lease_changed", 409)
            if current["status"] == "active":
                return True
            busy = connection.execute(
                select(slots.c.id).where(*self._scope(), slots.c.status == "active").limit(1)
            ).first()
            first = connection.execute(
                select(slots.c.id)
                .where(*self._scope(), slots.c.status == "queued")
                .order_by(slots.c.queue_position, slots.c.id)
                .limit(1)
            ).scalar_one_or_none()
            if busy is not None or first != identity:
                return False
            connection.execute(
                update(slots)
                .where(slots.c.id == identity, slots.c.status == "queued")
                .values(
                    status="active",
                    reason_code="meet_capacity_admitted",
                    queue_position=None,
                    deadline_at=turn["deadline"] + 5,
                )
            )
            return True

    def finish(self, identity, turn, now, *, uncertain=False):
        with self._locked() as connection:
            current = connection.execute(select(slots).where(*self._scope(), slots.c.id == identity)).mappings().first()
            if current is None or current["lease_metadata"] != binding(turn):
                raise MeetError("meet_capacity_lease_changed", 409)
            if current["status"] not in {"queued", "active"}:
                return
            values = (
                {"reason_code": "meet_capacity_execution_uncertain"}
                if uncertain
                else {
                    "status": "released",
                    "reason_code": "meet_capacity_finished",
                    "released_at": now,
                }
            )
            connection.execute(update(slots).where(slots.c.id == identity).values(**values))
