"""Immutable scoped source-policy persistence, independent of media kind."""

import hashlib

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError


def _scope(table, tenant, project):
    return (table.c.tenant_id == tenant, table.c.project_id == project)


def _actor(actor):
    if not isinstance(actor, str) or not 0 < len(actor) <= 255 or any(ord(c) < 32 or ord(c) == 127 for c in actor):
        raise ValueError("persona_policy_actor_invalid")


class SqlPersonaSourcePolicies:
    def __init__(self, engine, *, metadata, heads, versions, policy_type):
        self.engine, self.metadata = engine, metadata
        self.heads, self.versions, self.policy_type = heads, versions, policy_type

    def initialize(self):
        self.metadata.create_all(self.engine)

    def install(self, policy, *, expected_revision, actor):
        _actor(actor)
        if type(policy) is not self.policy_type:
            raise ValueError("persona_policy_media_kind_mismatch")
        if type(expected_revision) is not int or expected_revision < 0 or policy.revision != expected_revision + 1:
            raise ValueError("persona_policy_revision_invalid")
        payload = policy.model_dump_json()
        if len(payload.encode()) > 24_576:
            raise ValueError("persona_policy_too_large")
        self.policy_type.model_validate_json(payload)
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
                    connection.execute(insert(self.heads).values(**values))
                else:
                    changed = connection.execute(
                        update(self.heads)
                        .where(
                            *_scope(self.heads, policy.tenant_id, policy.project_id),
                            self.heads.c.source_id == policy.source.source_id,
                            self.heads.c.policy_binding == policy.policy_binding,
                            self.heads.c.revision == expected_revision,
                        )
                        .values(revision=policy.revision)
                    )
                    if changed.rowcount != 1:
                        raise ValueError("persona_policy_conflict")
                connection.execute(
                    insert(self.versions).values(
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
                    select(self.versions)
                    .join(
                        self.heads,
                        (self.versions.c.tenant_id == self.heads.c.tenant_id)
                        & (self.versions.c.project_id == self.heads.c.project_id)
                        & (self.versions.c.policy_binding == self.heads.c.policy_binding)
                        & (self.versions.c.revision == self.heads.c.revision),
                    )
                    .where(
                        *_scope(self.heads, tenant, project),
                        self.heads.c.source_id == source_id,
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
        policy = self.policy_type.model_validate_json(row["payload"])
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
                update(self.heads)
                .where(
                    *_scope(self.heads, tenant, project),
                    self.heads.c.source_id == source_id,
                    self.heads.c.revision == expected_revision,
                )
                .values(revision=expected_revision)
            )
            if changed.rowcount != 1:
                raise ValueError("persona_policy_conflict")
            head = (
                connection.execute(
                    select(self.heads).where(
                        *_scope(self.heads, tenant, project),
                        self.heads.c.source_id == source_id,
                        self.heads.c.revision == expected_revision,
                    )
                )
                .mappings()
                .first()
            )
            if head is None:
                raise ValueError("persona_policy_conflict")
            result = connection.execute(
                update(self.versions)
                .where(
                    *_scope(self.versions, tenant, project),
                    self.versions.c.policy_binding == head["policy_binding"],
                    self.versions.c.revision == expected_revision,
                    self.versions.c.state == "active",
                )
                .values(state="revoked", revoked_by=actor)
            )
            if result.rowcount != 1:
                raise ValueError("persona_policy_conflict")
            # Reserve a tombstone revision with no active policy payload.
            # Installations that started before revocation are now stale; an
            # explicit regrant needs the returned revision and a new version.
            connection.execute(
                update(self.heads)
                .where(
                    *_scope(self.heads, tenant, project),
                    self.heads.c.source_id == source_id,
                    self.heads.c.revision == expected_revision,
                )
                .values(revision=expected_revision + 1)
            )
        return expected_revision + 1
