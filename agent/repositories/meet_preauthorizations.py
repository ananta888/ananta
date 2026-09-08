"""Hub-owned policy CAS and burned dispatch allowances, serialized in SQL."""

import re
import time
import uuid

from sqlalchemy import JSON, BigInteger, Column, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent.models.meet_preauthorization_binding import policy_binding, scope_key, validate_policy_binding
from agent.models.meet_preauthorization_policy import MeetPreauthorizationPolicy, digest, identifier, integer, timestamp
from agent.services.meet_contract import MeetError

_metadata = MetaData()
policies = Table(
    "meet_dialog_preauthorizations",
    _metadata,
    Column("scope_key", String(64), primary_key=True),
    Column("policy_id", String(160), nullable=False, unique=True),
    Column("revision", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("used_dispatches", Integer, nullable=False),
    Column("lock_version", BigInteger, nullable=False),
    Column("document", JSON, nullable=False),
)
dispatches = Table(
    "meet_dialog_preauthorized_dispatches",
    _metadata,
    Column("task_id", String(160), primary_key=True),
    Column("scope_key", String(64), nullable=False),
    Column("binding", JSON, nullable=False),
)
events = Table(
    "meet_dialog_preauthorization_events",
    _metadata,
    Column("event_id", String(36), primary_key=True),
    Column("policy_id", String(160), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("document_digest", String(64), nullable=False),
    Column("operator", String(64), nullable=False),
    Column("recorded_at_ms", BigInteger, nullable=False),
)


def _validate(row):
    if row is None:
        raise MeetError("meet_preauthorization_missing", 403)
    policy = MeetPreauthorizationPolicy.parse(row["document"])
    integer(row["revision"])
    integer(row["used_dispatches"], minimum=0, maximum=policy.max_dispatches)
    if row["scope_key"] != digest(policy.scope()) or row["policy_id"] != policy.policy_id:
        raise MeetError("meet_preauthorization_storage_invalid", 503)
    if row["status"] not in {"active", "revoked"}:
        raise MeetError("meet_preauthorization_storage_invalid", 503)
    return policy


def _receipt(row):
    return {
        "schema": "ananta.meet-preauthorization-operator-receipt.v1",
        "policy_id": row["policy_id"],
        "revision": row["revision"],
        "status": row["status"],
        "document_digest": digest(row["document"]),
        "dispatch_started": False,
        "trust_activated": False,
    }


class SqlMeetPreauthorizations:
    def __init__(self, engine, *, clock=time.time):
        self.engine, self.clock = engine, clock

    def initialize(self):
        _metadata.create_all(self.engine)

    def _audit(self, connection, row, operator):
        if type(operator) is not str or not re.fullmatch(r"local-uid:[0-9]{1,12}", operator):
            raise MeetError("meet_preauthorization_operator_invalid", 403)
        connection.execute(
            insert(events).values(
                event_id=str(uuid.uuid4()),
                policy_id=row["policy_id"],
                revision=row["revision"],
                status=row["status"],
                document_digest=digest(row["document"]),
                operator=operator,
                recorded_at_ms=int(timestamp(self.clock()) * 1000),
            )
        )

    def provision(self, document, expected_revision, operator):
        policy = MeetPreauthorizationPolicy.parse(document)
        integer(expected_revision, minimum=0, maximum=2**31 - 2)
        if timestamp(self.clock()) >= policy.expires_at:
            raise MeetError("meet_preauthorization_expired", 403)
        key = digest(policy.scope())
        row = {
            "scope_key": key,
            "policy_id": policy.policy_id,
            "revision": expected_revision + 1,
            "status": "active",
            "used_dispatches": 0,
            "lock_version": 0,
            "document": policy.document(),
        }
        try:
            with self.engine.begin() as connection:
                if expected_revision == 0:
                    connection.execute(insert(policies).values(**row))
                else:
                    result = connection.execute(
                        update(policies)
                        .where(
                            (policies.c.scope_key == key)
                            & (policies.c.revision == expected_revision)
                            & (policies.c.policy_id == policy.policy_id)
                        )
                        .values(**row)
                    )
                    if result.rowcount != 1:
                        raise MeetError("meet_preauthorization_conflict", 409)
                if timestamp(self.clock()) >= policy.expires_at:
                    raise MeetError("meet_preauthorization_expired", 403)
                self._audit(connection, row, operator)
                return _receipt(row)
        except IntegrityError:
            raise MeetError("meet_preauthorization_conflict", 409) from None
        except SQLAlchemyError:
            raise MeetError("meet_preauthorization_storage_unavailable", 503) from None

    def revoke(self, policy_id, expected_revision, operator):
        identifier(policy_id)
        integer(expected_revision, maximum=2**31 - 2)
        try:
            with self.engine.begin() as connection:
                changed = connection.execute(
                    update(policies)
                    .where(
                        (policies.c.policy_id == policy_id)
                        & (policies.c.revision == expected_revision)
                        & (policies.c.status == "active")
                    )
                    .values(status="revoked", revision=expected_revision + 1)
                )
                if changed.rowcount != 1:
                    raise MeetError("meet_preauthorization_conflict", 409)
                row = connection.execute(select(policies).where(policies.c.policy_id == policy_id)).mappings().one()
                _validate(row)
                self._audit(connection, row, operator)
                return _receipt(row)
        except SQLAlchemyError:
            raise MeetError("meet_preauthorization_storage_unavailable", 503) from None

    def reserve(self, assignment):
        key = scope_key(assignment)
        try:
            with self.engine.begin() as connection:
                # This write obtains a real transaction lock on SQLite too.
                # Policy revision is unaffected; its only owner is the operator.
                connection.execute(
                    update(policies).where(policies.c.scope_key == key).values(lock_version=policies.c.lock_version + 1)
                )
                row = connection.execute(select(policies).where(policies.c.scope_key == key)).mappings().first()
                policy = _validate(row)
                if row["status"] != "active" or row["used_dispatches"] >= policy.max_dispatches:
                    raise MeetError("meet_preauthorization_inactive_or_exhausted", 403)
                policy.require_assignment(assignment, self.clock(), starting=True)
                binding = policy_binding(policy.policy_id, row["revision"], digest(assignment))
                connection.execute(
                    insert(dispatches).values(task_id=assignment["task_id"], scope_key=key, binding=binding)
                )
                connection.execute(
                    update(policies)
                    .where(policies.c.scope_key == key)
                    .values(used_dispatches=policies.c.used_dispatches + 1)
                )
                return binding
        except IntegrityError:
            raise MeetError("meet_preauthorization_dispatch_replayed", 409) from None
        except SQLAlchemyError:
            raise MeetError("meet_preauthorization_storage_unavailable", 503) from None

    def require_current(self, assignment, binding):
        binding = validate_policy_binding(binding)
        key = scope_key(assignment)
        try:
            with self.engine.connect() as connection:
                # One SQL snapshot binds policy and burned dispatch together.
                row = (
                    connection.execute(
                        select(policies, dispatches.c.binding)
                        .join(dispatches, dispatches.c.scope_key == policies.c.scope_key)
                        .where((policies.c.scope_key == key) & (dispatches.c.task_id == assignment["task_id"]))
                    )
                    .mappings()
                    .first()
                )
                policy = _validate(row)
                if (
                    row["status"] != "active"
                    or row["binding"] != binding
                    or binding != policy_binding(policy.policy_id, row["revision"], digest(assignment))
                ):
                    raise MeetError("meet_preauthorization_binding_denied", 403)
                policy.require_assignment(assignment, self.clock())
        except SQLAlchemyError:
            raise MeetError("meet_preauthorization_storage_unavailable", 503) from None
