"""Immutable scoped tool observations and bounded replay admission in Hub SQL."""

import hashlib

from sqlalchemy import Column, Integer, LargeBinary, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent.services.meet_contract import MeetError

metadata = MetaData()
sources = Table("meet_companion_tool_sources", metadata,
                Column("scope", String(64), primary_key=True),
                Column("digest", String(64), primary_key=True),
                Column("content", LargeBinary, nullable=False))
budgets = Table("meet_companion_tool_budgets", metadata,
                Column("assignment", String(64), primary_key=True),
                Column("calls", Integer, nullable=False))
receipts = Table("meet_companion_tool_receipts", metadata,
                 Column("assignment", String(64), primary_key=True),
                 Column("nonce", String(32), primary_key=True))


def _scope(tenant, project):
    # Length framing prevents boundary collisions without exposing identities.
    value = f"{len(tenant)}:{tenant}{len(project)}:{project}"
    return hashlib.sha256(value.encode()).hexdigest()


class SqlCompanionToolAttempts:
    def __init__(self, engine, *, max_calls=240):
        if type(max_calls) is not int or not 1 <= max_calls <= 240:
            raise ValueError("meet_companion_tool_budget_invalid")
        self.engine, self.max_calls = engine, max_calls

    def initialize(self):
        metadata.create_all(self.engine, tables=[budgets, receipts])

    def claim(self, assignment_digest, nonce):
        """One attempt consumes one Hub-owned budget slot, including failures."""
        try:
            with self.engine.begin() as connection:
                # A savepoint keeps a concurrent first-budget insert from
                # aborting the outer transaction on PostgreSQL.
                try:
                    with connection.begin_nested():
                        connection.execute(insert(budgets).values(assignment=assignment_digest, calls=0))
                except IntegrityError:
                    pass
                connection.execute(insert(receipts).values(assignment=assignment_digest, nonce=nonce))
                admitted = connection.execute(update(budgets).where(
                    (budgets.c.assignment == assignment_digest) & (budgets.c.calls < self.max_calls)
                ).values(calls=budgets.c.calls + 1))
                if admitted.rowcount != 1:
                    raise MeetError("meet_companion_tool_budget_exhausted", 429)
        except IntegrityError:
            raise MeetError("meet_companion_tool_replayed", 409) from None
        except SQLAlchemyError:
            raise MeetError("meet_companion_tool_store_unavailable", 503) from None

class SqlCompanionToolSources:
    def __init__(self, engine, *, maximum=32_000):
        if type(maximum) is not int or not 1 <= maximum <= 128_000:
            raise ValueError("meet_companion_source_limit_invalid")
        self.engine, self.maximum = engine, maximum

    def initialize(self):
        metadata.create_all(self.engine, tables=[sources])

    def read(self, tenant, project, digest):
        """Only already admitted, unchanged bytes may supply a later model run."""
        try:
            with self.engine.connect() as connection:
                content = connection.execute(select(sources.c.content).where(
                    (sources.c.scope == _scope(tenant, project)) & (sources.c.digest == digest)
                )).scalar_one_or_none()
        except SQLAlchemyError:
            raise MeetError("meet_companion_tool_store_unavailable", 503) from None
        if content is None or hashlib.sha256(content).hexdigest() != digest:
            raise MeetError("meet_companion_tool_source_unavailable_or_mutated", 403)
        return content

    def admit(self, tenant, project, content):
        if not isinstance(content, bytes) or not 0 < len(content) <= self.maximum:
            raise MeetError("meet_companion_tool_source_size_invalid", 502)
        digest = hashlib.sha256(content).hexdigest()
        scope = _scope(tenant, project)
        try:
            with self.engine.begin() as connection:
                try:
                    with connection.begin_nested():
                        connection.execute(insert(sources).values(scope=scope, digest=digest, content=content))
                except IntegrityError:
                    pass
                actual = connection.execute(select(sources.c.content).where(
                    (sources.c.scope == scope) & (sources.c.digest == digest)
                )).scalar_one()
                if actual != content:
                    raise MeetError("meet_companion_tool_source_mutated", 409)
        except SQLAlchemyError:
            raise MeetError("meet_companion_tool_store_unavailable", 503) from None
        return digest
