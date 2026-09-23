"""Remembered public directory entries, keyed by the room server that owns them."""

from sqlalchemy import Column, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError

_metadata = MetaData()
public_rooms = Table(
    "meet_public_rooms",
    _metadata,
    Column("server_origin", String(255), primary_key=True),
    Column("project_id", String(160), primary_key=True),
    Column("task_id", String(160), primary_key=True),
    Column("room_id", String(32), nullable=False),
)


class SqlPublicRoomStore:
    """Cache only; the room server stays the authority over the directory."""

    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    def _key(self, server_origin, project, task):
        return (
            (public_rooms.c.server_origin == server_origin)
            & (public_rooms.c.project_id == project)
            & (public_rooms.c.task_id == task)
        )

    def get(self, server_origin, project, task):
        with self.engine.connect() as connection:
            row = connection.execute(select(public_rooms).where(self._key(server_origin, project, task))).first()
            return row.room_id if row else None

    def _update(self, server_origin, project, task, room_id):
        with self.engine.begin() as connection:
            return connection.execute(
                update(public_rooms).where(self._key(server_origin, project, task)).values(room_id=room_id)
            ).rowcount

    def remember(self, server_origin, project, task, room_id):
        # Last writer wins: a concurrent creator already produced a usable
        # public room, so neither id is wrong and no revision is at stake.
        if self._update(server_origin, project, task, room_id):
            return
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(public_rooms).values(
                        server_origin=server_origin, project_id=project, task_id=task, room_id=room_id
                    )
                )
        except IntegrityError:
            self._update(server_origin, project, task, room_id)
