import pytest

from agent.services.sfu_broadcast_command_service import (
    InMemorySfuBroadcastCommandLedger,
    SfuBroadcastCommand,
    SfuBroadcastCommandAuthorization,
    SfuBroadcastCommandError,
    SfuBroadcastCommandExecution,
    SfuBroadcastCommandPrincipal,
    SfuBroadcastCommandService,
)


class _Authorizer:
    allowed = True

    def authorize(self, principal, command):
        return SfuBroadcastCommandAuthorization(self.allowed, "sfu_command_room_forbidden")


class _Executor:
    def __init__(self):
        self.calls = []

    def execute(self, principal, command, audit_event):
        self.calls.append((principal, command, audit_event))
        return SfuBroadcastCommandExecution(True, command.expected_version + 1, "active", "sfu_broadcast_started", True)


def _service():
    authorizer, executor = _Authorizer(), _Executor()
    service = SfuBroadcastCommandService(
        authorizer=authorizer,
        executor=executor,
        ledger=InMemorySfuBroadcastCommandLedger(),
        diagnostic_secret=b"c" * 32,
    )
    return service, authorizer, executor


def _command(**changes):
    values = dict(room_ref="room-a", action="start", expected_version=2, confirmed=True, options={"quality_preference": "medium"})
    values.update(changes)
    return SfuBroadcastCommand(**values)


def test_command_is_authorized_executed_and_replayed_exactly_once():
    service, _, executor = _service()
    principal = SfuBroadcastCommandPrincipal("user-a", "tenant-a", "user", ("room-a",))
    first = service.execute(principal, _command(), idempotency_key="command-key-0001")
    replay = service.execute(principal, _command(), idempotency_key="command-key-0001")
    assert first.accepted and replay.replayed
    assert first.command_ref == replay.command_ref
    assert len(executor.calls) == 1
    event = executor.calls[0][2]
    assert "user-a" not in event.actor_diagnostic_ref
    assert "room-a" not in event.room_diagnostic_ref


def test_conflict_authorization_confirmation_and_atomic_audit_fail_closed():
    service, authorizer, executor = _service()
    principal = SfuBroadcastCommandPrincipal("user-a", "tenant-a", "user", ("room-a",))
    service.execute(principal, _command(), idempotency_key="command-key-0002")
    with pytest.raises(SfuBroadcastCommandError, match="idempotency_conflict"):
        service.execute(principal, _command(expected_version=3), idempotency_key="command-key-0002")
    authorizer.allowed = False
    with pytest.raises(SfuBroadcastCommandError, match="room_forbidden"):
        service.execute(principal, _command(), idempotency_key="command-key-0003")
    with pytest.raises(SfuBroadcastCommandError, match="confirmation_required"):
        service.execute(principal, _command(confirmed=False), idempotency_key="command-key-0004")

    with pytest.raises(SfuBroadcastCommandError, match="expected_version_invalid"):
        service.execute(principal, _command(expected_version="2"), idempotency_key="command-key-0006")
    with pytest.raises(SfuBroadcastCommandError, match="sfu_command_invalid"):
        service.execute(principal, _command(action={"start": True}), idempotency_key="command-key-0007")

    authorizer.allowed = True
    executor.execute = lambda *_args: SfuBroadcastCommandExecution(True, 3, "active", "sfu_broadcast_started", False)
    with pytest.raises(SfuBroadcastCommandError, match="executor_result_invalid"):
        service.execute(principal, _command(), idempotency_key="command-key-0005")


def test_ambiguous_executor_failure_retries_same_operation_after_delivery_lease():
    clock = [100.0]
    authorizer = _Authorizer()
    operation_ids = []

    class _AmbiguousExecutor:
        def execute(self, principal, command, audit_event):
            operation_ids.append(audit_event.operation_id)
            if len(operation_ids) == 1:
                raise RuntimeError("transport_lost_after_commit")
            return SfuBroadcastCommandExecution(
                True, 3, "active", "sfu_broadcast_started", True
            )

    service = SfuBroadcastCommandService(
        authorizer=authorizer,
        executor=_AmbiguousExecutor(),
        ledger=InMemorySfuBroadcastCommandLedger(delivery_retry_seconds=5),
        diagnostic_secret=b"c" * 32,
        clock=lambda: clock[0],
    )
    principal = SfuBroadcastCommandPrincipal(
        "user-a", "tenant-a", "user", ("room-a",)
    )
    with pytest.raises(SfuBroadcastCommandError, match="executor_unavailable"):
        service.execute(principal, _command(), idempotency_key="command-key-0008")
    with pytest.raises(SfuBroadcastCommandError, match="command_in_progress"):
        service.execute(principal, _command(), idempotency_key="command-key-0008")
    clock[0] += 5
    assert service.execute(
        principal, _command(), idempotency_key="command-key-0008"
    ).accepted
    assert len(operation_ids) == 2
    assert operation_ids[0] == operation_ids[1]
