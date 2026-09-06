"""Durable, exact-asset retention grants and cross-Hub execution claims."""

import uuid

from sqlalchemy import BigInteger, Column, Index, MetaData, String, Table, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

_metadata = MetaData()
retention = Table(
    "persona_image_retention",
    _metadata,
    *(Column(name, String(160), primary_key=True) for name in ("tenant_id", "project_id", "artifact_id")),
    Column("revision", BigInteger, nullable=False),
    Column("asset_revision", BigInteger, nullable=False),
    Column("asset_digest", String(64), nullable=False),
    Column("actor", String(255), nullable=False),
    Column("due_at_ms", BigInteger, nullable=False),
    Column("next_attempt_ms", BigInteger, nullable=False),
    Column("state", String(16), nullable=False),
    Column("attempts", BigInteger, nullable=False),
    Column("task_id", String(160)),
    Column("lease_id", String(36)),
    Column("lease_until_ms", BigInteger, nullable=False),
)
events = Table(
    "persona_image_retention_events",
    _metadata,
    Column("event_id", String(36), primary_key=True),
    *(Column(name, String(160), nullable=False) for name in ("tenant_id", "project_id", "artifact_id")),
    Column("revision", BigInteger, nullable=False),
    Column("actor", String(255), nullable=False),
    Column("grant_actor", String(255), nullable=False),
    Column("asset_revision", BigInteger, nullable=False),
    Column("asset_digest", String(64), nullable=False),
    Column("due_at_ms", BigInteger, nullable=False),
    Column("lease_id", String(36)),
    Column("lease_until_ms", BigInteger, nullable=False),
    Column("state", String(16), nullable=False),
    Column("task_id", String(160)),
)
Index("ix_persona_retention_due", retention.c.state, retention.c.next_attempt_ms)


def scope(record):
    return {name: record[name] for name in ("tenant_id", "project_id", "artifact_id")}


def _where(record):
    return tuple(retention.c[name] == value for name, value in scope(record).items())


def _event(connection, record, actor=None):
    connection.execute(
        insert(events).values(
            event_id=str(uuid.uuid4()),
            **scope(record),
            revision=record["revision"],
            actor=actor or record["actor"],
            grant_actor=record["actor"],
            asset_revision=record["asset_revision"],
            asset_digest=record["asset_digest"],
            due_at_ms=record["due_at_ms"],
            lease_id=record["lease_id"],
            lease_until_ms=record["lease_until_ms"],
            state=record["state"],
            task_id=record["task_id"],
        )
    )


class SqlPersonaRetention:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    def get(self, key):
        with self.engine.connect() as connection:
            row = connection.execute(select(retention).where(*_where(key))).mappings().first()
            if row is None:
                raise ValueError("persona_retention_unavailable")
            return dict(row)

    def install(self, record, *, expected_revision):
        try:
            with self.engine.begin() as connection:
                if expected_revision == 0:
                    connection.execute(insert(retention).values(**record))
                else:
                    changed = connection.execute(
                        update(retention)
                        .where(
                            *_where(record),
                            retention.c.revision == expected_revision,
                            retention.c.state.in_(("scheduled", "cancelled", "blocked")),
                        )
                        .values(**record)
                    )
                    if changed.rowcount != 1:
                        raise ValueError("persona_retention_revision_conflict")
                _event(connection, record)
        except IntegrityError:
            raise ValueError("persona_retention_revision_conflict") from None

    def cancel(self, key, *, expected_revision, actor):
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(retention)
                .where(
                    *_where(key),
                    retention.c.revision == expected_revision,
                    retention.c.state.in_(("scheduled", "running", "blocked")),
                )
                .values(state="cancelled", revision=expected_revision + 1)
            )
            if changed.rowcount != 1:
                raise ValueError("persona_retention_revision_conflict")
            row = connection.execute(select(retention).where(*_where(key))).mappings().one()
            _event(connection, row, actor)
        return expected_revision + 1

    def due(self, now_ms, *, limit):
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("persona_retention_batch_invalid")
        with self.engine.connect() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    select(retention)
                    .where(
                        retention.c.due_at_ms <= now_ms,
                        retention.c.next_attempt_ms <= now_ms,
                        or_(
                            retention.c.state == "scheduled",
                            (retention.c.state == "running") & (retention.c.lease_until_ms <= now_ms),
                        ),
                    )
                    .order_by(
                        retention.c.next_attempt_ms,
                        retention.c.tenant_id,
                        retention.c.project_id,
                        retention.c.artifact_id,
                    )
                    .limit(limit)
                ).mappings()
            )

    def claim(self, observed, now_ms):
        record = observed | dict(
            state="running",
            attempts=observed["attempts"] + 1,
            task_id="persona-retention-" + str(uuid.uuid4()),
            lease_id=str(uuid.uuid4()),
            lease_until_ms=now_ms + 60_000,
            next_attempt_ms=now_ms + 60_000,
        )
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(retention)
                .where(
                    *_where(observed),
                    retention.c.revision == observed["revision"],
                    retention.c.attempts == observed["attempts"],
                    retention.c.state == observed["state"],
                    retention.c.due_at_ms <= now_ms,
                    retention.c.next_attempt_ms <= now_ms,
                    or_(
                        retention.c.state == "scheduled",
                        (retention.c.state == "running") & (retention.c.lease_until_ms <= now_ms),
                    ),
                )
                .values(**record)
            )
            if changed.rowcount != 1:
                return None
            _event(connection, record)
        return record

    def require_claim(self, record, now_ms):
        current = self.get(record)
        if current != record or current["state"] != "running" or now_ms >= current["lease_until_ms"]:
            raise PermissionError("persona_retention_claim_changed")

    def finish(self, record, state, now_ms):
        if state not in ("completed", "blocked", "scheduled"):
            raise ValueError("persona_retention_terminal_invalid")
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(retention)
                .where(
                    *_where(record),
                    retention.c.revision == record["revision"],
                    retention.c.state == "running",
                    retention.c.lease_id == record["lease_id"],
                    retention.c.task_id == record["task_id"],
                    retention.c.lease_until_ms > now_ms if state == "completed" else True,
                )
                .values(state=state, next_attempt_ms=now_ms + min(3600, 30 * 2 ** min(record["attempts"], 6)) * 1000)
            )
            if changed.rowcount == 1:
                _event(connection, record | {"state": state})
            return changed.rowcount == 1
