"""Serialized durable resource leases for existing Hub dialog Tasks."""

import re
from contextlib import contextmanager

from sqlalchemy import BigInteger, Column, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from agent.db_models import TaskDB, WorkerSlotLeaseDB
from agent.models.meet_dialog_capacity import DialogCapacityPolicy, digest
from agent.services.meet_contract import MeetError

_metadata = MetaData()
pools = Table(
    "meet_dialog_capacity_pools",
    _metadata,
    Column("id", String(128), primary_key=True),
    Column("sequence", BigInteger, nullable=False),
    Column("policy_digest", String(64), nullable=False),
)
slots, tasks = WorkerSlotLeaseDB.__table__, TaskDB.__table__
_TYPE = "meet_dialog_capacity"


class SqlDialogCapacity:
    def __init__(self, engine, pool, policy: DialogCapacityPolicy):
        if not isinstance(pool, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", pool):
            raise ValueError("meet_dialog_capacity_pool_invalid")
        self.engine, self.pool, self.policy = engine, pool, policy
        self.policy_digest = digest(policy.projection())

    def initialize(self):
        _metadata.create_all(self.engine)
        slots.create(self.engine, checkfirst=True)
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(pools).values(id=self.pool, sequence=0, policy_digest=self.policy_digest))
        except IntegrityError:
            pass
        with self._locked():
            pass  # Refuse a differently configured existing pool even at restart.

    @contextmanager
    def _locked(self):
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(pools)
                .where(pools.c.id == self.pool, pools.c.policy_digest == self.policy_digest)
                .values(sequence=pools.c.sequence)
            )
            if changed.rowcount != 1:
                raise MeetError("meet_dialog_capacity_pool_changed", 503)
            yield connection

    def _scope(self):
        return (slots.c.worker_id == self.pool, slots.c.lease_type == _TYPE)

    def _expire(self, connection, now):
        connection.execute(
            update(slots)
            .where(*self._scope(), slots.c.status.in_(("queued", "active")), slots.c.deadline_at <= now)
            .values(status="stale_released", reason_code="meet_dialog_capacity_expired", released_at=now)
        )
        # A terminal Task is not proof of stopped browser descendants. The
        # supervisor's longest startup allowance is 90s, followed by 5s cleanup.
        rows = connection.execute(
            select(slots.c.id, slots.c.deadline_at)
            .outerjoin(tasks, tasks.c.id == slots.c.parent_task_id)
            .where(
                *self._scope(),
                slots.c.status == "active",
                slots.c.reason_code != "meet_dialog_capacity_terminal_quarantine",
                (tasks.c.id.is_(None)) | (tasks.c.status != "in_progress"),
            )
        ).all()
        for identity, deadline in rows:
            connection.execute(
                update(slots)
                .where(slots.c.id == identity)
                .values(deadline_at=min(deadline, now + 95), reason_code="meet_dialog_capacity_terminal_quarantine")
            )

    def reserve(self, binding, now):
        if not now < binding["deadline"] <= now + 7200:
            raise MeetError("meet_dialog_capacity_task_expired", 409)
        if not self.policy.fits([], binding):
            raise MeetError("meet_dialog_capacity_profile_exceeded", 429)
        identity = digest([_TYPE, self.pool, binding])
        with self._locked() as connection:
            self._expire(connection, now)
            if connection.execute(select(slots.c.id).where(slots.c.id == identity)).first():
                raise MeetError("meet_dialog_capacity_duplicate_dispatch", 409)
            waiting = connection.execute(
                select(slots.c.id).where(*self._scope(), slots.c.status == "queued").limit(4)
            ).all()
            if len(waiting) >= 4:
                raise MeetError("meet_dialog_capacity_queue_full", 429)
            sequence = connection.execute(select(pools.c.sequence).where(pools.c.id == self.pool)).scalar_one() + 1
            if sequence >= 2**31:
                raise MeetError("meet_dialog_capacity_sequence_exhausted", 503)
            connection.execute(update(pools).where(pools.c.id == self.pool).values(sequence=sequence))
            lease = WorkerSlotLeaseDB(
                id=identity,
                lease_type=_TYPE,
                status="queued",
                worker_id=self.pool,
                worker_kind="meet_dialog",
                runtime_kind="private_container",
                parent_task_id=binding["task_id"],
                queue_position=sequence,
                acquired_at=now,
                deadline_at=min(binding["deadline"], now + 10),
                reason_code="meet_dialog_capacity_waiting",
                lease_metadata=binding,
            )
            connection.execute(insert(slots).values(**lease.model_dump()))
        return identity

    def poll(self, identity, binding, now):
        with self._locked() as connection:
            self._expire(connection, now)
            current = self._current(connection, identity, binding)
            if current["status"] not in {"queued", "active"}:
                raise MeetError("meet_dialog_capacity_lease_changed", 409)
            if current["status"] == "active":
                return True
            first = connection.execute(
                select(slots.c.id)
                .where(*self._scope(), slots.c.status == "queued")
                .order_by(slots.c.queue_position, slots.c.id)
                .limit(1)
            ).scalar_one_or_none()
            active = self._active(connection)
            if first != identity or not self.policy.fits(active, binding):
                return False
            connection.execute(
                update(slots)
                .where(slots.c.id == identity)
                .values(
                    status="active",
                    queue_position=None,
                    deadline_at=binding["deadline"] + 5,
                    reason_code="meet_dialog_capacity_admitted",
                )
            )
            return True

    def _active(self, connection):
        rows = connection.execute(
            select(slots.c.id, slots.c.lease_metadata)
            .where(*self._scope(), slots.c.status == "active")
            .limit(self.policy.sessions + 1)
        ).all()
        try:
            if len(rows) > self.policy.sessions or any(
                digest([_TYPE, self.pool, binding]) != identity for identity, binding in rows
            ):
                raise ValueError()
        except (TypeError, ValueError):
            raise MeetError("meet_dialog_capacity_state_changed", 503) from None
        return [binding for _identity, binding in rows]

    def _current(self, connection, identity, binding):
        row = connection.execute(select(slots).where(*self._scope(), slots.c.id == identity)).mappings().first()
        if row is None or row["lease_metadata"] != binding:
            raise MeetError("meet_dialog_capacity_lease_changed", 409)
        return row

    def cancel_before_dispatch(self, identity, binding, now):
        with self._locked() as connection:
            current = self._current(connection, identity, binding)
            if current["status"] in {"queued", "active"}:
                connection.execute(
                    update(slots)
                    .where(slots.c.id == identity)
                    .values(
                        status="released",
                        released_at=now,
                        reason_code="meet_dialog_capacity_not_dispatched",
                    )
                )
