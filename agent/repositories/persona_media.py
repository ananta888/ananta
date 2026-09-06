"""Immutable persona revisions; access decisions belong to the Hub service."""

import json

from sqlalchemy import BigInteger, Column, MetaData, String, Table, Text, insert, select, update
from sqlalchemy.exc import IntegrityError

from agent.models.persona_media import PersonaMediaProfile

_metadata = MetaData()


def _scope_columns():
    return [
        Column(name, String(160), primary_key=True) for name in ("tenant_id", "project_id", "owner_kind", "owner_id")
    ]


profiles = Table(
    "persona_media_profiles",
    _metadata,
    *_scope_columns(),
    Column("revision", BigInteger, primary_key=True),
    Column("content_hash", String(64), nullable=False),
    Column("payload", Text, nullable=False),
)
heads = Table(
    "persona_media_heads",
    _metadata,
    *_scope_columns(),
    Column("revision", BigInteger, nullable=False),
)
events = Table(
    "persona_media_profile_events",
    _metadata,
    *_scope_columns(),
    Column("revision", BigInteger, primary_key=True),
    Column("actor", String(191), nullable=True),
    Column("content_hash", String(64), nullable=False),
)


def _scope(profile):
    return {name: getattr(profile, name) for name in ("tenant_id", "project_id", "owner_kind", "owner_id")}


def _where(table, scope):
    return tuple(table.c[name] == value for name, value in scope.items())


class SqlPersonaProfiles:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    def append(self, profile: PersonaMediaProfile, *, expected_revision: int, actor=None):
        if type(expected_revision) is not int or expected_revision < 0 or profile.revision != expected_revision + 1:
            raise ValueError("persona_revision_invalid")
        payload = profile.model_dump_json()
        if len(payload.encode()) > 16_384:
            raise ValueError("persona_profile_too_large")
        scope = _scope(profile)
        try:
            with self.engine.begin() as connection:
                if expected_revision == 0:
                    connection.execute(insert(heads).values(**scope, revision=1))
                else:
                    changed = connection.execute(
                        update(heads)
                        .where(*_where(heads, scope), heads.c.revision == expected_revision)
                        .values(revision=profile.revision)
                    )
                    if changed.rowcount != 1:
                        raise ValueError("persona_revision_conflict")
                connection.execute(
                    insert(profiles).values(
                        **scope,
                        revision=profile.revision,
                        content_hash=profile.content_hash(),
                        payload=payload,
                    )
                )
                connection.execute(
                    insert(events).values(
                        **scope, revision=profile.revision, actor=actor, content_hash=profile.content_hash()
                    )
                )
        except IntegrityError:
            raise ValueError("persona_revision_conflict") from None
        return profile.content_hash()

    def current(self, *, tenant_id, project_id, owner_kind, owner_id):
        scope = dict(tenant_id=tenant_id, project_id=project_id, owner_kind=owner_kind, owner_id=owner_id)
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(heads.c.revision, profiles.c.content_hash)
                    .select_from(
                        heads.outerjoin(
                            profiles,
                            (
                                (heads.c.revision == profiles.c.revision)
                                & (heads.c.tenant_id == profiles.c.tenant_id)
                                & (heads.c.project_id == profiles.c.project_id)
                                & (heads.c.owner_kind == profiles.c.owner_kind)
                                & (heads.c.owner_id == profiles.c.owner_id)
                            ),
                        )
                    )
                    .where(*_where(heads, scope))
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        if row["content_hash"] is None:
            raise ValueError("persona_profile_integrity_failed")
        return self.get(**scope, revision=row["revision"], content_hash=row["content_hash"])

    def get(self, *, tenant_id, project_id, owner_kind, owner_id, revision, content_hash):
        scope = dict(tenant_id=tenant_id, project_id=project_id, owner_kind=owner_kind, owner_id=owner_id)
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(profiles).where(
                        *_where(profiles, scope),
                        profiles.c.revision == revision,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ValueError("persona_profile_unavailable")
        try:
            if len(row["payload"].encode()) > 16_384:
                raise ValueError("invalid")
            profile = PersonaMediaProfile.model_validate(json.loads(row["payload"]))
            if (
                _scope(profile) != scope
                or profile.revision != revision
                or profile.content_hash() != row["content_hash"]
                or profile.content_hash() != content_hash
            ):
                raise ValueError("invalid")
            return profile
        except (ValueError, TypeError):
            raise ValueError("persona_profile_integrity_failed") from None
