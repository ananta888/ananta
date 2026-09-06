"""Immutable policy versions with scoped CAS heads and durable revocation."""

import hashlib

from sqlalchemy import BigInteger, Column, MetaData, String, Table, Text, insert, select, update
from sqlalchemy.exc import IntegrityError

from agent.models.persona_asset_policy import PersonaImagePolicy

_metadata = MetaData()
heads = Table(
    "persona_image_policy_heads",
    _metadata,
    Column("tenant_id", String(160), primary_key=True),
    Column("project_id", String(160), primary_key=True),
    Column("source_id", String(160), primary_key=True),
    Column("policy_binding", String(160), nullable=False),
    Column("revision", BigInteger, nullable=False),
)
versions = Table(
    "persona_image_policy_versions",
    _metadata,
    Column("tenant_id", String(160), primary_key=True),
    Column("project_id", String(160), primary_key=True),
    Column("policy_binding", String(160), primary_key=True),
    Column("revision", BigInteger, primary_key=True),
    Column("state", String(16), nullable=False),
    Column("payload", Text, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("created_by", String(255), nullable=False),
    Column("revoked_by", String(255), nullable=True),
)


def _scope(table, tenant, project):
    return (table.c.tenant_id == tenant, table.c.project_id == project)


def _actor(actor):
    if not isinstance(actor, str) or not 0 < len(actor) <= 255 or any(ord(c) < 32 or ord(c) == 127 for c in actor):
        raise ValueError("persona_policy_actor_invalid")


class SqlPersonaImagePolicies:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    def install(self, policy: PersonaImagePolicy, *, expected_revision, actor):
        _actor(actor)
        if type(expected_revision) is not int or expected_revision < 0 or policy.revision != expected_revision + 1:
            raise ValueError("persona_policy_revision_invalid")
        payload = policy.model_dump_json()
        if len(payload.encode()) > 24_576:
            raise ValueError("persona_policy_too_large")
        values = dict(
            tenant_id=policy.tenant_id,
            project_id=policy.project_id,
            source_id=policy.source.source_id,
            policy_binding=policy.policy_binding,
            revision=policy.revision,
        )
        try:
            with self.engine.begin() as connection:
                if expected_revision == 0:
                    connection.execute(insert(heads).values(**values))
                else:
                    changed = connection.execute(
                        update(heads)
                        .where(
                            *_scope(heads, policy.tenant_id, policy.project_id),
                            heads.c.source_id == policy.source.source_id,
                            heads.c.policy_binding == policy.policy_binding,
                            heads.c.revision == expected_revision,
                        )
                        .values(revision=policy.revision)
                    )
                    if changed.rowcount != 1:
                        raise ValueError("persona_policy_conflict")
                connection.execute(
                    insert(versions).values(
                        tenant_id=policy.tenant_id,
                        project_id=policy.project_id,
                        policy_binding=policy.policy_binding,
                        revision=policy.revision,
                        state="active",
                        payload=payload,
                        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                        created_by=actor,
                    )
                )
        except IntegrityError:
            raise ValueError("persona_policy_conflict") from None

    def for_source(self, tenant, project, source_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(versions)
                    .join(
                        heads,
                        (versions.c.tenant_id == heads.c.tenant_id)
                        & (versions.c.project_id == heads.c.project_id)
                        & (versions.c.policy_binding == heads.c.policy_binding)
                        & (versions.c.revision == heads.c.revision),
                    )
                    .where(
                        *_scope(heads, tenant, project),
                        heads.c.source_id == source_id,
                    )
                )
                .mappings()
                .first()
            )
        if row is None or row["state"] != "active":
            raise ValueError("persona_policy_unavailable")
        if (
            len(row["payload"].encode()) > 24_576
            or hashlib.sha256(row["payload"].encode()).hexdigest() != row["payload_sha256"]
        ):
            raise ValueError("persona_policy_integrity_failed")
        policy = PersonaImagePolicy.model_validate_json(row["payload"])
        if (policy.tenant_id, policy.project_id, policy.source.source_id, policy.policy_binding, policy.revision) != (
            tenant,
            project,
            source_id,
            row["policy_binding"],
            row["revision"],
        ):
            raise ValueError("persona_policy_integrity_failed")
        return policy

    def revoke(self, tenant, project, source_id, *, expected_revision, actor):
        _actor(actor)
        if type(expected_revision) is not int or not 1 <= expected_revision < 2**53 - 1:
            raise ValueError("persona_policy_revision_invalid")
        with self.engine.begin() as connection:
            # Lock/check the current head before changing its version row;
            # a concurrent install cannot turn a stale revoke into success.
            changed = connection.execute(
                update(heads)
                .where(
                    *_scope(heads, tenant, project),
                    heads.c.source_id == source_id,
                    heads.c.revision == expected_revision,
                )
                .values(revision=expected_revision)
            )
            if changed.rowcount != 1:
                raise ValueError("persona_policy_conflict")
            head = (
                connection.execute(
                    select(heads).where(
                        *_scope(heads, tenant, project),
                        heads.c.source_id == source_id,
                        heads.c.revision == expected_revision,
                    )
                )
                .mappings()
                .first()
            )
            if head is None:
                raise ValueError("persona_policy_conflict")
            result = connection.execute(
                update(versions)
                .where(
                    *_scope(versions, tenant, project),
                    versions.c.policy_binding == head["policy_binding"],
                    versions.c.revision == expected_revision,
                    versions.c.state == "active",
                )
                .values(state="revoked", revoked_by=actor)
            )
            if result.rowcount != 1:
                raise ValueError("persona_policy_conflict")
            # Reserve a tombstone revision with no active policy payload.
            # Installations that started before revocation are now stale; an
            # explicit regrant needs the returned revision and a new version.
            connection.execute(
                update(heads)
                .where(
                    *_scope(heads, tenant, project),
                    heads.c.source_id == source_id,
                    heads.c.revision == expected_revision,
                )
                .values(revision=expected_revision + 1)
            )
        return expected_revision + 1
