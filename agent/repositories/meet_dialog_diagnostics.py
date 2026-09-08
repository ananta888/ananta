"""Append-only Hub ledger; terminal Tasks themselves remain fully immutable."""

import time
from copy import deepcopy

from sqlalchemy import JSON, Column, MetaData, String, Table, insert, select
from sqlalchemy.exc import SQLAlchemyError

from agent.models.meet_dialog_diagnostics import TASK_FIELDS, task_binding, timestamp_ms, validate_record
from agent.services.meet_contract import MeetError

_metadata = MetaData()
observations = Table(
    "meet_dialog_terminal_observations",
    _metadata,
    Column("task_id", String(160), primary_key=True),
    Column("record", JSON, nullable=False),
)


class SqlDialogDiagnostics:
    def __init__(self, engine, *, clock=time.time):
        self.engine, self.clock = engine, clock

    def initialize(self):
        _metadata.create_all(self.engine)

    @staticmethod
    def _task(connection, task_id, *, lock=False):
        from agent.db_models import TaskDB

        table = TaskDB.__table__
        query = select(*(table.c[name] for name in TASK_FIELDS)).where(table.c.id == task_id)
        row = connection.execute(query.with_for_update() if lock else query).mappings().first()
        return deepcopy(dict(row)) if row is not None else None

    def read_task(self, task_id):
        try:
            with self.engine.connect() as connection:
                return self._task(connection, task_id)
        except SQLAlchemyError:
            raise MeetError("meet_dialog_diagnostics_storage_unavailable", 503) from None

    def read_record(self, task_id):
        try:
            with self.engine.connect() as connection:
                return deepcopy(
                    connection.execute(
                        select(observations.c.record).where(observations.c.task_id == task_id)
                    ).scalar_one_or_none()
                )
        except SQLAlchemyError:
            raise MeetError("meet_dialog_diagnostics_storage_unavailable", 503) from None

    def append(self, snapshot, record, valid_until_ms):
        record = validate_record(record, snapshot)
        try:
            with self.engine.begin() as connection:
                current = self._task(connection, snapshot["id"], lock=True)
                if current != snapshot or task_binding(current) != record["binding_digest"]:
                    raise MeetError("meet_dialog_diagnostics_conflict", 409)
                if type(valid_until_ms) is not int or timestamp_ms(self.clock()) >= valid_until_ms:
                    raise MeetError("meet_dialog_diagnostics_expired", 409)
                stored = connection.execute(
                    select(observations.c.record).where(observations.c.task_id == snapshot["id"])
                ).scalar_one_or_none()
                if stored is not None:
                    stored = validate_record(stored, snapshot)
                    if (
                        stored["observation_digest"] != record["observation_digest"]
                        or stored["observation"] != record["observation"]
                    ):
                        raise MeetError("meet_dialog_diagnostics_conflict", 409)
                    return stored
                connection.execute(insert(observations).values(task_id=snapshot["id"], record=record))
                return record
        except SQLAlchemyError:
            # An uncertain insert is not permission to write again or alter Tasks.
            raise MeetError("meet_dialog_diagnostics_storage_unavailable", 503) from None
