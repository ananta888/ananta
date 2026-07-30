from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlmodel import SQLModel, Session, create_engine, select

from agent.db_models.source_access_enforcement import (
    SourceAccessGrantConsumptionDB,
    SourceAccessGrantExecutionPolicyDB,
)
from agent.db_models.source_control import SourceAccessGrantDB
from agent.services.source_access_persistence_adapter import (
    SQLSourceAccessEnforcementAdapter,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _adapter(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'source-access.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SourceAccessGrantExecutionPolicyDB.__table__,
            SourceAccessGrantConsumptionDB.__table__,
            SourceAccessGrantDB.__table__,
        ],
    )
    return engine, SQLSourceAccessEnforcementAdapter(
        engine,
        clock=lambda: NOW,
    )


def _seed_active_grant(engine, grant_id):
    values = {}
    for column in SourceAccessGrantDB.__table__.columns:
        name = column.name
        if name == "grant_id":
            values[name] = grant_id
        elif name == "state":
            values[name] = "active"
        elif name == "expires_at":
            values[name] = NOW + timedelta(minutes=5)
        elif name.endswith("_at"):
            values[name] = NOW
        elif name in {"version", "grant_version", "lock_version"}:
            values[name] = 1
        elif name == "source_revision_id":
            values[name] = "srev_" + "7" * 64
        elif name == "destination_id":
            values[name] = "dst_" + "8" * 64
        elif name == "operation":
            values[name] = "index"
        elif name == "transformation":
            values[name] = "redacted"
        elif name == "purpose":
            values[name] = "knowledge-index"
        elif name == "policy_version":
            values[name] = "policy-v1"
        elif name == "tenant_id":
            values[name] = "tenant-test"
        elif name == "project_id":
            values[name] = "project-test"
        elif name == "grant_family_id":
            values[name] = f"family-{grant_id}"
        elif (
            not column.nullable
            and column.default is None
            and column.server_default is None
        ):
            if isinstance(column.type, sa.JSON):
                values[name] = {}
            elif isinstance(column.type, sa.Boolean):
                values[name] = False
            elif isinstance(column.type, sa.Integer):
                values[name] = 1
            elif isinstance(column.type, sa.DateTime):
                values[name] = NOW
            else:
                values[name] = f"{name}-test"
    with engine.begin() as connection:
        connection.execute(
            SourceAccessGrantDB.__table__.insert().values(**values)
        )


def test_one_time_grant_is_consumed_exactly_once(tmp_path) -> None:
    engine, adapter = _adapter(tmp_path)
    _seed_active_grant(engine, "grant-" + "a" * 64)
    adapter.bind_execution_policy(
        grant_id="grant-" + "a" * 64,
        grant_digest="b" * 64,
        destination_digest="c" * 64,
        consumption_mode="one_time",
    )

    first = adapter.consume_once(
        grant_id="grant-" + "a" * 64,
        expected_version=1,
        consumption_digest="d" * 64,
    )
    replay = adapter.consume_once(
        grant_id="grant-" + "a" * 64,
        expected_version=1,
        consumption_digest="d" * 64,
    )

    assert first is True
    assert replay is False
    with Session(engine) as session:
        consumptions = session.exec(
            select(SourceAccessGrantConsumptionDB)
        ).all()
        policy = session.get(
            SourceAccessGrantExecutionPolicyDB,
            "grant-" + "a" * 64,
        )
    assert len(consumptions) == 1
    assert policy is not None
    assert policy.concurrency_version == 2


def test_consumption_rejects_stale_version_and_reusable_policy(
    tmp_path,
) -> None:
    _engine, adapter = _adapter(tmp_path)
    _seed_active_grant(_engine, "grant-" + "e" * 64)
    _seed_active_grant(_engine, "grant-" + "2" * 64)
    adapter.bind_execution_policy(
        grant_id="grant-" + "e" * 64,
        grant_digest="f" * 64,
        destination_digest="1" * 64,
        consumption_mode="one_time",
    )
    adapter.bind_execution_policy(
        grant_id="grant-" + "2" * 64,
        grant_digest="3" * 64,
        destination_digest="4" * 64,
        consumption_mode="reusable",
    )

    assert not adapter.consume_once(
        grant_id="grant-" + "e" * 64,
        expected_version=2,
        consumption_digest="5" * 64,
    )
    assert not adapter.consume_once(
        grant_id="grant-" + "2" * 64,
        expected_version=1,
        consumption_digest="6" * 64,
    )
