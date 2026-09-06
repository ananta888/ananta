"""Opaque, short-lived, scope-bound paging handles; never authorization tokens."""

import hashlib
import re
import secrets
import time

from sqlalchemy import BigInteger, Column, MetaData, String, Table, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

_metadata = MetaData()


def _scope_columns():
    return [Column(name, String(255), primary_key=True) for name in ("tenant_id", "project_id", "subject_id")]


scopes = Table(
    "persona_image_cursor_scopes", _metadata, *_scope_columns(), Column("revision", BigInteger, nullable=False)
)
cursors = Table(
    "persona_image_cursors",
    _metadata,
    *_scope_columns(),
    Column("digest", String(64), primary_key=True),
    Column("position", String(160), nullable=False),
    Column("expires_at", BigInteger, nullable=False),
)


def _where(table, scope):
    return tuple(table.c[name] == value for name, value in scope.items())


class SqlPersonaImageCursors:
    def __init__(self, engine, *, clock=time.time):
        self.engine, self.clock = engine, clock

    def initialize(self):
        _metadata.create_all(self.engine)

    def resolve(self, *, tenant_id, project_id, subject_id, token):
        if token is None:
            return ""
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise ValueError("persona_image_cursor_invalid")
        scope = dict(tenant_id=tenant_id, project_id=project_id, subject_id=subject_id)
        with self.engine.connect() as connection:
            position = connection.execute(
                select(cursors.c.position).where(
                    *_where(cursors, scope),
                    cursors.c.digest == hashlib.sha256(token.encode()).hexdigest(),
                    cursors.c.expires_at > int(self.clock() * 1000),
                )
            ).scalar_one_or_none()
        if position is None:
            raise ValueError("persona_image_cursor_unavailable")
        return position

    def issue(self, *, tenant_id, project_id, subject_id, position):
        scope = dict(tenant_id=tenant_id, project_id=project_id, subject_id=subject_id)
        now = int(self.clock() * 1000)
        for attempt in range(2):
            try:
                with self.engine.begin() as connection:
                    # Serialize only this reader's paging state across Hub
                    # replicas; never lock the whole project or asset catalog.
                    changed = connection.execute(
                        update(scopes).where(*_where(scopes, scope)).values(revision=scopes.c.revision + 1)
                    )
                    if changed.rowcount == 0:
                        connection.execute(insert(scopes).values(**scope, revision=1))
                    connection.execute(delete(cursors).where(*_where(cursors, scope), cursors.c.expires_at <= now))
                    count = connection.execute(
                        select(func.count()).select_from(cursors).where(*_where(cursors, scope))
                    ).scalar_one()
                    if count >= 128:
                        raise ValueError("persona_image_cursor_budget_exceeded")
                    token = secrets.token_urlsafe(32)
                    connection.execute(
                        insert(cursors).values(
                            **scope,
                            digest=hashlib.sha256(token.encode()).hexdigest(),
                            position=position,
                            expires_at=now + 300_000,
                        )
                    )
                return token
            except IntegrityError:
                if attempt:
                    raise ValueError("persona_image_cursor_conflict") from None
        raise ValueError("persona_image_cursor_unavailable")
