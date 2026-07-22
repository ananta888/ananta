from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from agent.db_models.sfu_broadcast_user_intents import (
    SfuBroadcastCommandAuditDB,
    SfuBroadcastUserIntentDB,
)
from agent.repositories.sfu_broadcast_user_intent_repository import (
    SqlSfuBroadcastUserIntentRepository,
)
from agent.services.sfu_broadcast_command_execution import (
    SfuBroadcastCommandPolicyEvaluator,
)
from agent.services.sfu_broadcast_command_repository_port import (
    SfuBroadcastCommandMutation,
    SfuBroadcastCommandPolicyDecision,
)
from agent.services.sfu_broadcast_command_service import (
    InMemorySfuBroadcastCommandLedger,
    SfuBroadcastCommand,
    SfuBroadcastCommandAuthorization,
    SfuBroadcastCommandExecution,
    SfuBroadcastCommandPrincipal,
    SfuBroadcastCommandService,
)


@dataclass
class _Projection:
    version: int = 7
    available: bool = True
    flags: object = None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self):
        if self.flags is None:
            self.flags = {"semantic_media_broadcast": True}


@dataclass
class _Scope:
    admission_epoch: int = 4
    membership_epoch: int = 9


class _Policy:
    def __init__(self, projection=None):
        self.projection = projection or _Projection()

    def effective(self, tenant_id):
        return self.projection


class _Authority:
    def __init__(self, scope=None):
        self.scope = _Scope() if scope is None else scope

    def resolve(self, **kwargs):
        return self.scope


def _command(action="start", expected_version=0, options=None):
    return SfuBroadcastCommand(
        room_ref="room-1",
        action=action,
        expected_version=expected_version,
        confirmed=True,
        options=options or {},
    )


def test_policy_enforces_room_scope_feature_and_kill_switch() -> None:
    principal = SfuBroadcastCommandPrincipal(
        subject="actor-1", tenant_ref="tenant-1", role="user", room_scopes=()
    )
    evaluator = SfuBroadcastCommandPolicyEvaluator(
        feature_policy=_Policy(), room_authority=_Authority()
    )
    assert evaluator.evaluate(principal, _command()).allowed is False

    scoped = SfuBroadcastCommandPrincipal(
        subject="actor-1",
        tenant_ref="tenant-1",
        role="user",
        room_scopes=("room-1",),
    )
    assert evaluator.evaluate(scoped, _command()).allowed is True
    killed = SfuBroadcastCommandPolicyEvaluator(
        feature_policy=_Policy(
            _Projection(reason_codes=("immediate_security_fence",))
        ),
        room_authority=_Authority(),
    )
    assert killed.evaluate(scoped, _command()).execution_reason == (
        "sfu_broadcast_kill_switch_active"
    )
    assert killed.evaluate(scoped, _command("stop")).allowed is True


class _AllowCommands:
    def authorize(self, principal, command):
        return SfuBroadcastCommandAuthorization(
            allowed=True, reason_code="sfu_broadcast_command_authorized"
        )


class _CaptureOperations:
    def __init__(self):
        self.operation_ids = []

    def execute(self, principal, command, audit_event):
        self.operation_ids.append(audit_event.operation_id)
        return SfuBroadcastCommandExecution(
            accepted=True,
            effective_version=1,
            state="active",
            reason_code="sfu_broadcast_started",
            audit_committed=True,
        )


def test_operation_id_is_bound_to_tenant_principal_and_idempotency_key() -> None:
    executor = _CaptureOperations()
    service = SfuBroadcastCommandService(
        authorizer=_AllowCommands(),
        executor=executor,
        ledger=InMemorySfuBroadcastCommandLedger(),
        diagnostic_secret=b"operation-scope-test-secret-0000001",
    )
    principals = (
        SfuBroadcastCommandPrincipal("actor-1", "tenant-1", "admin"),
        SfuBroadcastCommandPrincipal("actor-2", "tenant-1", "admin"),
        SfuBroadcastCommandPrincipal("actor-1", "tenant-2", "admin"),
    )
    for index, principal in enumerate(principals):
        service.execute(
            principal,
            _command(),
            idempotency_key=f"operation-key-{index:08d}",
        )
    service.execute(
        principals[0],
        _command(),
        idempotency_key="operation-key-99999999",
    )
    assert len(set(executor.operation_ids)) == 4


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            SfuBroadcastUserIntentDB.__table__,
            SfuBroadcastCommandAuditDB.__table__,
        ],
    )
    return engine


def _mutation(
    *,
    operation="sfcop1.operation-0000000000000001",
    tenant="tenant-1",
    expected=0,
    now=None,
):
    instant = now or datetime.now(timezone.utc)
    return SfuBroadcastCommandMutation(
        tenant_id=tenant,
        room_id="room-1",
        tenant_diagnostic_ref="tenant-diagnostic-ref-1",
        room_diagnostic_ref="room-diagnostic-ref-01",
        actor_diagnostic_ref="actor-diagnostic-ref-1",
        actor_role="operator",
        operation_id=operation,
        request_digest=(operation.encode().hex() + "0" * 64)[:64],
        action="start",
        reason="user_requested",
        expected_version=expected,
        policy=SfuBroadcastCommandPolicyDecision(
            allowed=True,
            authorization_reason="sfu_broadcast_command_authorized",
            execution_reason="sfu_broadcast_command_noop",
            policy_version=7,
            admission_epoch=4,
            membership_epoch=9,
        ),
        data_saver=False,
        audio_only=False,
        quality_preference="auto",
        now=instant,
        retain_until=instant + timedelta(days=30),
    )


def test_sql_repository_replay_restart_cas_cross_tenant_and_content_free_audit() -> None:
    engine = _engine()
    first_repository = SqlSfuBroadcastUserIntentRepository(db_engine=engine)
    mutation = _mutation()
    first = first_repository.execute(mutation)
    assert (first.accepted, first.effective_version, first.state) == (True, 1, "active")

    restarted_repository = SqlSfuBroadcastUserIntentRepository(db_engine=engine)
    replay = restarted_repository.execute(mutation)
    assert replay.replayed is True
    assert replay.effective_version == 1

    stale = restarted_repository.execute(
        _mutation(operation="sfcop1.operation-0000000000000002", expected=0)
    )
    assert stale.reason_code == "sfu_broadcast_version_conflict"
    other_tenant = restarted_repository.execute(
        _mutation(
            operation="sfcop1.operation-0000000000000003", tenant="tenant-2"
        )
    )
    assert other_tenant.effective_version == 1

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SfuBroadcastUserIntentDB)) == 2
        audit = session.get(SfuBroadcastCommandAuditDB, mutation.operation_id)
        assert audit.actor_diagnostic_ref != "actor-1"
        assert not hasattr(audit, "payload")
