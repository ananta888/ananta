"""SQL persistence with monotonic CAS revisions, including unlink tombstones."""

from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from agent.services.meet_contract import MeetError, MeetingBinding

_metadata = MetaData()
bindings = Table(
    "meet_bindings",
    _metadata,
    Column("provider_origin", String(255), primary_key=True),
    Column("tenant_id", String(160), primary_key=True),
    Column("project_id", String(160), primary_key=True),
    Column("task_id", String(160), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("room_id", String(32), nullable=True),
)
events = Table(
    "meet_binding_events",
    _metadata,
    Column("provider_origin", String(255), primary_key=True),
    Column("tenant_id", String(160), primary_key=True),
    Column("project_id", String(160), primary_key=True),
    Column("task_id", String(160), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("actor", String(255), nullable=False),
    Column("action", String(16), nullable=False),
)


class SqlMeetingStore:
    def __init__(self, engine, provider_origin):
        self.engine = engine
        self.provider_origin = provider_origin

    def initialize(self):
        _metadata.create_all(self.engine)

    def _key(self, tenant, project, task):
        return (
            (bindings.c.provider_origin == self.provider_origin)
            & (bindings.c.tenant_id == tenant)
            & (bindings.c.project_id == project)
            & (bindings.c.task_id == task)
        )

    def get(self, tenant, project, task):
        with self.engine.connect() as connection:
            row = connection.execute(select(bindings).where(self._key(tenant, project, task))).first()
            return MeetingBinding(row.revision, row.room_id) if row else MeetingBinding()

    def replace(self, tenant, project, task, expected, room_id, actor):
        # Even a repeated desired value requires the current revision: retries
        # cannot acknowledge an intervening unlink/reattach (ABA).
        try:
            with self.engine.begin() as connection:
                if expected == 0:
                    connection.execute(
                        insert(bindings).values(
                            provider_origin=self.provider_origin,
                            tenant_id=tenant,
                            project_id=project,
                            task_id=task,
                            revision=1,
                            room_id=room_id,
                        )
                    )
                else:
                    result = connection.execute(
                        update(bindings)
                        .where(self._key(tenant, project, task) & (bindings.c.revision == expected))
                        .values(revision=expected + 1, room_id=room_id)
                    )
                    if result.rowcount != 1:
                        raise MeetError("meet_binding_conflict", 409)
                connection.execute(
                    insert(events).values(
                        provider_origin=self.provider_origin,
                        tenant_id=tenant,
                        project_id=project,
                        task_id=task,
                        revision=expected + 1,
                        actor=actor,
                        action="unlink" if room_id is None else "attach",
                    )
                )
        except IntegrityError as exc:
            raise MeetError("meet_binding_conflict", 409) from exc
        return MeetingBinding(expected + 1, room_id)
