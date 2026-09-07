"""Shared SQL grant/CAS/audit persistence, with separately composed media tables."""

import uuid

from sqlalchemy import insert, or_, select, update
from sqlalchemy.exc import IntegrityError


def scope(record):
    return {name: record[name] for name in ("tenant_id", "project_id", "artifact_id")}


class SqlPersonaRetentionStore:
    def __init__(self, engine, *, metadata, retention, events):
        self.engine, self.metadata, self.retention, self.events = engine, metadata, retention, events

    def initialize(self):
        self.metadata.create_all(self.engine)

    def get(self, key):
        with self.engine.connect() as connection:
            row = connection.execute(select(self.retention).where(*self._where(key))).mappings().first()
            if row is None:
                raise ValueError("persona_retention_unavailable")
            return dict(row)

    def install(self, record, *, expected_revision):
        try:
            with self.engine.begin() as connection:
                if expected_revision == 0:
                    connection.execute(insert(self.retention).values(**record))
                else:
                    changed = connection.execute(
                        update(self.retention)
                        .where(
                            *self._where(record),
                            self.retention.c.revision == expected_revision,
                            self.retention.c.state.in_(("scheduled", "cancelled", "blocked")),
                        )
                        .values(**record)
                    )
                    if changed.rowcount != 1:
                        raise ValueError("persona_retention_revision_conflict")
                self._event(connection, record)
        except IntegrityError:
            raise ValueError("persona_retention_revision_conflict") from None

    def cancel(self, key, *, expected_revision, actor):
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(self.retention)
                .where(
                    *self._where(key),
                    self.retention.c.revision == expected_revision,
                    self.retention.c.state.in_(("scheduled", "running", "blocked")),
                )
                .values(state="cancelled", revision=expected_revision + 1)
            )
            if changed.rowcount != 1:
                raise ValueError("persona_retention_revision_conflict")
            row = connection.execute(select(self.retention).where(*self._where(key))).mappings().one()
            self._event(connection, row, actor)
        return expected_revision + 1

    def due(self, now_ms, *, limit):
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("persona_retention_batch_invalid")
        with self.engine.connect() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    select(self.retention)
                    .where(
                        self.retention.c.due_at_ms <= now_ms,
                        self.retention.c.next_attempt_ms <= now_ms,
                        or_(
                            self.retention.c.state == "scheduled",
                            (self.retention.c.state == "running") & (self.retention.c.lease_until_ms <= now_ms),
                        ),
                    )
                    .order_by(
                        self.retention.c.next_attempt_ms,
                        self.retention.c.tenant_id,
                        self.retention.c.project_id,
                        self.retention.c.artifact_id,
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
                update(self.retention)
                .where(
                    *self._where(observed),
                    self.retention.c.revision == observed["revision"],
                    self.retention.c.attempts == observed["attempts"],
                    self.retention.c.state == observed["state"],
                    self.retention.c.due_at_ms <= now_ms,
                    self.retention.c.next_attempt_ms <= now_ms,
                    or_(
                        self.retention.c.state == "scheduled",
                        (self.retention.c.state == "running") & (self.retention.c.lease_until_ms <= now_ms),
                    ),
                )
                .values(**record)
            )
            if changed.rowcount != 1:
                return None
            self._event(connection, record)
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
                update(self.retention)
                .where(
                    *self._where(record),
                    self.retention.c.revision == record["revision"],
                    self.retention.c.state == "running",
                    self.retention.c.lease_id == record["lease_id"],
                    self.retention.c.task_id == record["task_id"],
                    self.retention.c.lease_until_ms > now_ms if state == "completed" else True,
                )
                .values(state=state, next_attempt_ms=now_ms + min(3600, 30 * 2 ** min(record["attempts"], 6)) * 1000)
            )
            if changed.rowcount == 1:
                self._event(connection, record | {"state": state})
            return changed.rowcount == 1

    def _where(self, record):
        return tuple(self.retention.c[name] == value for name, value in scope(record).items())

    def _event(self, connection, record, actor=None):
        connection.execute(
            insert(self.events).values(
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
