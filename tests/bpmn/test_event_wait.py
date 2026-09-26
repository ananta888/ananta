"""Synthetic wait-component observations, not production or end-to-end evidence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from agent.services.bpmn_event_wait import BpmnEventWaitService
from agent.services.bpmn_event_wait_contracts import (
    CatchActivation,
    EarlyMessagePolicy,
    EventWaitConflict,
    EventWaitError,
    InboundMessage,
    MessageContract,
    TimerSpec,
    WaitLimits,
    WaitRunBinding,
)
from agent.services.bpmn_event_wait_store import WAIT_CHECKPOINT_TASK, CheckpointEventWaitStore
from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    OptimisticConcurrencyError,
    SignatureValidationError,
)
from agent.services.workflow_runtime.persistence import InMemoryCheckpointStore, SQLiteCheckpointStore, SQLiteEventStore
from agent.services.workflow_runtime.security import HmacKeyRing

RUN = WaitRunBinding("tenant-a", "project-a", "workflow-a", "run-a", "revision-1", "a" * 64, "policy-1")
TARGET = CatchActivation("catch", "activation-1")
CONTRACT = MessageContract("order.received", "order-42", "order.v1")


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class AllowExactTarget:
    """Synthetic authenticated Hub policy; target came from a reserved activation."""

    def authorize(self, message):
        if message.run != RUN or message.target != TARGET or message.contract != CONTRACT:
            raise PermissionError("message_scope_denied")


class ValidateOrder:
    def validate(self, schema_id, payload):
        if schema_id != "order.v1" or set(payload) != {"value"} or type(payload["value"]) is not int:
            raise ValueError("message_schema_denied")


def message(**values):
    return replace(InboundMessage(RUN, TARGET, CONTRACT, "msg-1", 100, 130, {"value": 7}), **values)


@pytest.fixture(params=["memory", "sqlite"])
def env(request, tmp_path):
    keys = HmacKeyRing({"synthetic": b"synthetic-test-signing-material-only"}, active_key_id="synthetic")
    path = tmp_path / "waits.sqlite"
    stores = []
    clock = Clock()

    def open_store():
        if request.param == "memory":
            if not stores:
                stores.append(InMemoryCheckpointStore())
            return stores[0]
        store = SQLiteCheckpointStore(path)
        stores.append(store)
        return store

    def service(**overrides):
        values = dict(
            run=RUN,
            store=CheckpointEventWaitStore(open_store(), keys),
            clock=clock,
            fencing_token=1,
            authorizer=AllowExactTarget(),
            payload_validator=ValidateOrder(),
        )
        values.update(overrides)
        return BpmnEventWaitService(**values)

    yield service, clock, stores, keys
    for store in stores:
        if isinstance(store, SQLiteCheckpointStore):
            store.close()


def test_timer_is_durable_and_does_not_reset_on_repeated_registration(env):
    service, clock, *_ = env
    hub = service()
    armed = hub.arm_timer(TARGET, TimerSpec(duration_seconds=10), timeout_seconds=50)
    assert armed.status == "waiting" and armed.due_at == 110 and armed.expires_at == 150
    assert hub.pending() == ()
    clock.now = 109
    assert service().arm_timer(TARGET, TimerSpec(duration_seconds=10), timeout_seconds=50) == armed
    clock.now = 112
    restarted = service()
    (first,) = restarted.pending()
    assert first.ready_at == 112 and first.wakeup_id == armed.wakeup_id
    assert restarted.pending() == (first,)
    receipt = restarted.consume(TARGET, wakeup_id=first.wakeup_id)
    clock.now = 999
    assert service().consume(TARGET, wakeup_id=first.wakeup_id) == receipt
    assert service().pending() == ()
    assert service().inspect(TARGET).status == "consumed"


def test_timer_timezone_normalization_and_absolute_overdue_instant(env):
    service, clock, *_ = env
    utc = TimerSpec.at("2026-09-24T10:00:00Z")
    assert TimerSpec.at("2026-09-24T12:00:00+02:00") == utc
    clock.now = utc.due_at + 1
    view = service().arm_timer(TARGET, utc, timeout_seconds=30)
    assert view.due_at == utc.due_at and view.status == "ready"


@pytest.mark.parametrize("raw", ["2026-09-24T10:00:00", "2026-09-24", "R/PT1S", "not-a-date", 42])
def test_timer_date_rejects_ambiguous_or_unsupported_forms(raw):
    with pytest.raises(EventWaitError):
        TimerSpec.at(raw)


@pytest.mark.parametrize("raw", [True, -1, float("nan"), float("inf")])
def test_invalid_duration_is_rejected(raw):
    with pytest.raises(EventWaitError):
        TimerSpec(duration_seconds=raw)


def test_timer_bounds_and_activation_definition_drift(env):
    service, _, stores, _ = env
    hub = service()
    for timer, timeout in [(TimerSpec(duration_seconds=30), 30), (TimerSpec(duration_seconds=1), 90000)]:
        with pytest.raises(EventWaitError):
            hub.arm_timer(TARGET, timer, timeout_seconds=timeout)
    assert stores[0].get_latest(tenant_id=RUN.tenant_id, run_id=RUN.run_id, task_id=WAIT_CHECKPOINT_TASK) is None
    hub.arm_timer(TARGET, TimerSpec(duration_seconds=10), timeout_seconds=30)
    with pytest.raises(EventWaitConflict, match="activation_definition_conflict"):
        hub.arm_timer(TARGET, TimerSpec(duration_seconds=11), timeout_seconds=30)
    with pytest.raises(EventWaitConflict):
        hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=30)
    other = CatchActivation(TARGET.element_id, "activation-2")
    assert hub.arm_timer(other, TimerSpec(duration_seconds=0), timeout_seconds=30).status == "ready"
    assert hub.inspect(TARGET).status == "waiting"


@pytest.mark.parametrize("ready_first", [False, True])
def test_expiry_is_terminal_at_exact_deadline_even_after_ready(env, ready_first):
    service, clock, *_ = env
    hub = service()
    view = hub.arm_timer(TARGET, TimerSpec(duration_seconds=10), timeout_seconds=20)
    if ready_first:
        clock.now = 110
        assert len(hub.pending()) == 1
    clock.now = 120
    assert service().pending() == ()
    assert service().inspect(TARGET).status == "expired"
    assert service().consume(TARGET, wakeup_id=view.wakeup_id) is None
    clock.now = 105
    assert service().inspect(TARGET).status == "expired"


def test_cancellation_prevents_ready_wakeup_and_is_idempotent(env):
    service, clock, *_ = env
    hub = service()
    view = hub.arm_timer(TARGET, TimerSpec(duration_seconds=10), timeout_seconds=50)
    clock.now = 110
    assert len(hub.pending()) == 1
    assert hub.cancel(TARGET).status == "cancelled"
    assert service().cancel(TARGET).status == "cancelled"
    assert service().pending() == ()
    assert service().consume(TARGET, wakeup_id=view.wakeup_id) is None


def test_run_cancellation_closes_empty_run_and_buffered_messages(env):
    service, _, *_ = env
    hub = service(early_messages=EarlyMessagePolicy(10, 2))
    hub.deliver_message(message())
    hub.cancel_run()
    assert hub.deliver_message(message()).status == "cancelled"
    assert service().cancel_run() == ()
    with pytest.raises(EventWaitError, match="run_closed"):
        service().arm_timer(TARGET, TimerSpec(duration_seconds=1), timeout_seconds=20)
    with pytest.raises(EventWaitError, match="run_closed"):
        hub.deliver_message(message(message_id="new"))


def test_message_after_subscription_restarts_and_dedupes_without_mutable_aliases(env):
    service, clock, *_ = env
    hub = service()
    hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=50)
    incoming = message()
    assert hub.deliver_message(incoming).status == "matched"
    incoming.payload["value"] = 900
    restarted = service()
    assert restarted.deliver_message(message()).status == "matched"
    (wakeup,) = restarted.pending()
    assert wakeup.payload == {"value": 7}
    wakeup.payload["value"] = 888
    receipt = restarted.consume(TARGET, wakeup_id=wakeup.wakeup_id)
    assert receipt.payload == {"value": 7}
    clock.now = 131
    assert service().deliver_message(message()).status == "matched"
    assert service().consume(TARGET, wakeup_id=wakeup.wakeup_id) == receipt


def test_early_buffer_requires_explicit_policy_and_matches_exact_subscription(env):
    service, _, *_ = env
    with pytest.raises(EventWaitError, match="early_message_denied"):
        service().deliver_message(message())
    hub = service(early_messages=EarlyMessagePolicy(10, 2))
    assert hub.deliver_message(message()).status == "buffered"
    wrong_activation = CatchActivation(TARGET.element_id, "activation-2")
    assert hub.subscribe_message(wrong_activation, CONTRACT, timeout_seconds=30).status == "waiting"
    restarted = service(early_messages=EarlyMessagePolicy(10, 2))
    assert restarted.subscribe_message(TARGET, CONTRACT, timeout_seconds=30).status == "ready"
    assert restarted.deliver_message(message()).status == "matched"
    assert len(restarted.pending()) == 1


def test_early_buffer_ttl_and_capacity_are_persistent(env):
    service, clock, *_ = env
    hub = service(early_messages=EarlyMessagePolicy(5, 1))
    hub.deliver_message(message())
    with pytest.raises(EventWaitError, match="buffer_capacity_exceeded"):
        hub.deliver_message(message(message_id="msg-2"))
    clock.now = 105
    restarted = service(early_messages=EarlyMessagePolicy(5, 1))
    assert restarted.deliver_message(message()).status == "expired"
    assert restarted.subscribe_message(TARGET, CONTRACT, timeout_seconds=20).status == "waiting"
    assert restarted.pending() == ()
    assert restarted.deliver_message(message(message_id="msg-2")).status == "matched"


def test_message_expiry_exact_deadline_and_no_deadline_extension(env):
    service, clock, *_ = env
    hub = service()
    original = hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=50)
    clock.now = 130
    assert hub.deliver_message(message()).status == "expired"
    assert hub.pending() == ()
    assert hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=50) == original
    clock.now = 150
    assert service().inspect(TARGET).status == "expired"


@pytest.mark.parametrize("change", [dict(payload={"value": 8}), dict(expires_at=131), dict(sent_at=99)])
def test_message_id_binds_entire_envelope(env, change):
    service, _, *_ = env
    hub = service()
    hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=50)
    hub.deliver_message(message())
    with pytest.raises(EventWaitConflict, match="message_id_conflict"):
        hub.deliver_message(message(**change))
    assert len(hub.pending()) == 1


@pytest.mark.parametrize(
    "change",
    [
        dict(tenant_id="tenant-b"),
        dict(project_id="project-b"),
        dict(run_id="run-b"),
        dict(workflow_id="workflow-b"),
        dict(definition_revision="revision-2"),
        dict(plan_hash="b" * 64),
        dict(policy_version="policy-2"),
    ],
)
def test_inbound_message_run_binding_is_exact_before_authorization(env, change):
    service, _, stores, _ = env
    hub = service()
    with pytest.raises(EventWaitConflict, match="message_run_binding_mismatch"):
        hub.deliver_message(message(run=replace(RUN, **change)))
    assert stores[0].get_latest(tenant_id=RUN.tenant_id, run_id=RUN.run_id, task_id=WAIT_CHECKPOINT_TASK) is None


@pytest.mark.parametrize(
    "change",
    [
        dict(target=CatchActivation("other", "activation-1")),
        dict(target=CatchActivation("catch", "activation-2")),
        dict(contract=replace(CONTRACT, correlation_key="other-order")),
        dict(contract=replace(CONTRACT, name="other-name")),
        dict(contract=replace(CONTRACT, schema_id="other.v1")),
    ],
)
def test_unauthorized_target_and_contract_do_not_mutate_run(env, change):
    service, _, stores, _ = env
    hub = service()
    hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=50)
    before = stores[0].get_latest(tenant_id=RUN.tenant_id, run_id=RUN.run_id, task_id=WAIT_CHECKPOINT_TASK)
    with pytest.raises(PermissionError):
        hub.deliver_message(message(**change))
    assert stores[0].get_latest(tenant_id=RUN.tenant_id, run_id=RUN.run_id, task_id=WAIT_CHECKPOINT_TASK) == before


def test_correlation_checked_independently_of_ingress_authorization(env):
    service, _, *_ = env
    hub = service()
    hub.subscribe_message(TARGET, replace(CONTRACT, correlation_key="other"), timeout_seconds=50)
    with pytest.raises(EventWaitConflict, match="correlation_mismatch"):
        hub.deliver_message(message())
    assert hub.pending() == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "approve", "tool": "shell"},
        {"value": "7"},
        {"value": float("nan")},
        {"value": 7, "api_key": "synthetic-do-not-persist"},
        {"value": object()},
        {1: 7},
    ],
)
def test_invalid_or_sensitive_payload_is_rejected_before_persistence(env, payload):
    service, _, stores, _ = env
    with pytest.raises(ValueError):
        service().deliver_message(message(payload=payload))
    assert stores[0].get_latest(tenant_id=RUN.tenant_id, run_id=RUN.run_id, task_id=WAIT_CHECKPOINT_TASK) is None


def test_message_admission_is_mandatory_and_mutating_adapters_fail_closed(env):
    service, _, *_ = env
    hub = service(authorizer=None)
    with pytest.raises(EventWaitError, match="admission_unavailable"):
        hub.deliver_message(message())
    with pytest.raises(EventWaitError, match="admission_unavailable"):
        hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=20)

    class MutatingValidator:
        def validate(self, schema_id, payload):
            payload["value"] = 42

    with pytest.raises(EventWaitError, match="admission_mutated_message"):
        service(payload_validator=MutatingValidator()).deliver_message(message())


@pytest.mark.parametrize(
    "change",
    [
        dict(project_id="other"),
        dict(definition_revision="revision-2"),
        dict(plan_hash="b" * 64),
        dict(workflow_id="other"),
        dict(policy_version="other"),
    ],
)
def test_signed_aggregate_binding_cannot_be_reinterpreted_after_restart(env, change):
    service, _, *_ = env
    service().arm_timer(TARGET, TimerSpec(duration_seconds=1), timeout_seconds=20)
    with pytest.raises((EventWaitConflict, SignatureValidationError)):
        service(run=replace(RUN, **change)).pending()


def test_tenant_and_run_storage_isolation_and_fencing(env):
    service, _, *_ = env
    hub = service()
    hub.arm_timer(TARGET, TimerSpec(duration_seconds=1), timeout_seconds=20)
    assert service(run=replace(RUN, tenant_id="tenant-b")).inspect(TARGET) is None
    assert service(run=replace(RUN, run_id="run-b")).inspect(TARGET) is None
    service(fencing_token=2).inspect(TARGET)
    with pytest.raises(FencingTokenError):
        hub.cancel(TARGET)


def test_wakeup_is_bound_to_activation_and_event_projection_dedupes(env, tmp_path):
    service, _, *_ = env
    hub = service()
    first = hub.arm_timer(TARGET, TimerSpec(duration_seconds=0), timeout_seconds=20)
    other = CatchActivation("catch", "activation-2")
    hub.arm_timer(other, TimerSpec(duration_seconds=0), timeout_seconds=20)
    with pytest.raises(EventWaitConflict, match="wakeup_binding_mismatch"):
        hub.consume(other, wakeup_id=first.wakeup_id)
    with pytest.raises(EventWaitError, match="not_consumed"):
        hub.pending()[0].to_event()
    receipt = hub.consume(TARGET, wakeup_id=first.wakeup_id)
    events = SQLiteEventStore(tmp_path / "events.sqlite")
    try:
        stored = events.append(receipt.to_event(), expected_sequence=0)
        replay = service().consume(TARGET, wakeup_id=first.wakeup_id)
        assert events.append(replay.to_event(), expected_sequence=999) == stored
        assert "payload" not in stored.payload
        assert len(events.list_events(tenant_id=RUN.tenant_id, run_id=RUN.run_id)) == 1
    finally:
        events.close()


class RacingStore:
    """Force independent readers onto one revision, with bounded rendezvous."""

    def __init__(self, store, barrier):
        self.store, self.barrier, self.first = store, barrier, True

    def load(self, run):
        value = self.store.load(run)
        if self.first:
            self.first = False
            self.barrier.wait(timeout=3)
        return value

    def commit(self, *args, **kwargs):
        self.store.commit(*args, **kwargs)


def racing_services(env):
    service, _, *_ = env
    barrier = Barrier(2)
    hubs = [service(), service()]
    for hub in hubs:
        hub._store = RacingStore(hub._store, barrier)
    return hubs


def test_cancel_consume_race_has_one_persisted_winner(env):
    service, _, *_ = env
    view = service().arm_timer(TARGET, TimerSpec(duration_seconds=0), timeout_seconds=50)
    consumer, canceller = racing_services(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        consume = pool.submit(consumer.consume, TARGET, wakeup_id=view.wakeup_id)
        cancel = pool.submit(canceller.cancel, TARGET)
        receipt, cancelled = consume.result(timeout=5), cancel.result(timeout=5)
    final = service().inspect(TARGET)
    if final.status == "cancelled":
        assert receipt is None and cancelled.status == "cancelled"
    else:
        assert final.status == cancelled.status == "consumed" and receipt is not None
    assert service().pending() == ()


def test_parallel_message_consumers_share_one_durable_receipt(env):
    service, _, *_ = env
    service().subscribe_message(TARGET, CONTRACT, timeout_seconds=50)
    a, b = racing_services(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(hub.deliver_message, message()) for hub in (a, b)]
        assert [result.result(timeout=5).status for result in results] == ["matched", "matched"]
    (ready,) = service().pending()
    a, b = racing_services(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(hub.consume, TARGET, wakeup_id=ready.wakeup_id) for hub in (a, b)]
        assert results[0].result(timeout=5) == results[1].result(timeout=5)
    assert service().pending() == ()


def test_crash_after_consume_commit_replays_receipt(env):
    service, _, *_ = env
    hub = service()
    view = hub.arm_timer(TARGET, TimerSpec(duration_seconds=0), timeout_seconds=50)

    class CommitThenCrash:
        def load(self, run):
            return underlying.load(run)

        def commit(self, *args, **kwargs):
            underlying.commit(*args, **kwargs)
            raise RuntimeError("synthetic_process_crash_after_commit")

    underlying = hub._store
    crashing = service(store=CommitThenCrash())
    with pytest.raises(RuntimeError, match="process_crash"):
        crashing.consume(TARGET, wakeup_id=view.wakeup_id)
    receipt = service().consume(TARGET, wakeup_id=view.wakeup_id)
    assert receipt is not None and receipt.consumed_at == 100
    assert service().consume(TARGET, wakeup_id=view.wakeup_id) == receipt


def test_contention_has_bounded_machine_readable_failure(env):
    service, _, *_ = env
    underlying = service()._store

    class Contended:
        calls = 0

        def load(self, run):
            return underlying.load(run)

        def commit(self, *args, **kwargs):
            self.calls += 1
            raise OptimisticConcurrencyError("synthetic_revision_conflict")

    contended = Contended()
    with pytest.raises(EventWaitConflict, match="contention_retry_exhausted"):
        service(store=contended, limits=WaitLimits(cas_attempts=2)).arm_timer(
            TARGET,
            TimerSpec(duration_seconds=1),
            timeout_seconds=20,
        )
    assert contended.calls == 2 and underlying.load(RUN).revision == 0


def test_capacity_keeps_tombstones_and_prevents_rearming(env):
    service, _, *_ = env
    hub = service(limits=WaitLimits(max_activations=1, max_messages=1))
    hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=50)
    hub.deliver_message(message())
    hub.cancel(TARGET)
    assert hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=50).status == "cancelled"
    with pytest.raises(EventWaitError, match="activation_capacity_exceeded"):
        hub.arm_timer(CatchActivation("other", "activation-1"), TimerSpec(duration_seconds=1), timeout_seconds=50)


def test_checkpoint_signature_tamper_is_rejected(env):
    service, _, stores, keys = env
    hub = service()
    hub.arm_timer(TARGET, TimerSpec(duration_seconds=1), timeout_seconds=20)
    original = stores[0].get_latest(tenant_id=RUN.tenant_id, run_id=RUN.run_id, task_id=WAIT_CHECKPOINT_TASK)

    class TamperedRead:
        def get_latest(self, **kwargs):
            original.state.business_data["closed"] = True
            return original

    with pytest.raises(SignatureValidationError):
        service(store=CheckpointEventWaitStore(TamperedRead(), keys)).pending()


def test_distinct_messages_racing_for_one_activation_have_one_winner(env):
    service, _, *_ = env
    service().subscribe_message(TARGET, CONTRACT, timeout_seconds=50)
    a, b = racing_services(env)

    def deliver(hub, identity):
        try:
            return hub.deliver_message(message(message_id=identity)).status
        except EventWaitError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(deliver, a, "first")
        second = pool.submit(deliver, b, "second")
        assert sorted([first.result(timeout=5), second.result(timeout=5)]) == [
            "bpmn_wait_not_waiting",
            "matched",
        ]
    assert len(service().pending()) == 1


def test_sqlite_store_close_and_reopen_recovers_timer_and_message(tmp_path):
    path = tmp_path / "closed-and-reopened.sqlite"
    keys = HmacKeyRing({"synthetic": b"synthetic-only-signing-material"}, active_key_id="synthetic")
    clock = Clock()

    def bind(store):
        return BpmnEventWaitService(
            run=RUN,
            store=CheckpointEventWaitStore(store, keys),
            clock=clock,
            fencing_token=1,
            authorizer=AllowExactTarget(),
            payload_validator=ValidateOrder(),
            early_messages=EarlyMessagePolicy(10, 2),
        )

    store = SQLiteCheckpointStore(path)
    try:
        hub = bind(store)
        hub.deliver_message(message())
        hub.arm_timer(CatchActivation("timer", "activation-1"), TimerSpec(duration_seconds=2), timeout_seconds=20)
    finally:
        store.close()
    clock.now = 105
    reopened = SQLiteCheckpointStore(path)
    try:
        hub = bind(reopened)
        assert hub.subscribe_message(TARGET, CONTRACT, timeout_seconds=20).status == "ready"
        assert {item.kind for item in hub.pending()} == {"message", "timer"}
    finally:
        reopened.close()


def test_payload_and_state_capacity_fail_without_partial_write(env):
    service, _, stores, _ = env
    for limits, reason in [
        (WaitLimits(max_payload_bytes=5), "payload_too_large"),
        (WaitLimits(max_state_bytes=50), "state_capacity_exceeded"),
    ]:
        hub = service(limits=limits, early_messages=EarlyMessagePolicy(5, 1))
        with pytest.raises(EventWaitError, match=reason):
            hub.deliver_message(message())
    assert stores[0].get_latest(tenant_id=RUN.tenant_id, run_id=RUN.run_id, task_id=WAIT_CHECKPOINT_TASK) is None


def test_message_budget_retains_expired_tombstones(env):
    service, clock, *_ = env
    hub = service(limits=WaitLimits(max_messages=1), early_messages=EarlyMessagePolicy(5, 1))
    hub.deliver_message(message())
    clock.now = 105
    assert hub.deliver_message(message()).status == "expired"
    with pytest.raises(EventWaitError, match="message_capacity_exceeded"):
        hub.deliver_message(message(message_id="second"))


@pytest.mark.parametrize(
    "change,reason",
    [
        (dict(sent_at=101), "message_from_future"),
        (dict(expires_at=4000), "message_ttl_exceeds_limit"),
    ],
)
def test_message_time_bounds(env, change, reason):
    service, _, *_ = env
    with pytest.raises(EventWaitError, match=reason):
        service(early_messages=EarlyMessagePolicy(5, 1)).deliver_message(message(**change))


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True, 0, -1])
def test_clock_is_validated_before_commit(env, invalid):
    service, clock, stores, _ = env
    clock.now = invalid
    with pytest.raises(EventWaitError, match="clock_invalid"):
        service().arm_timer(TARGET, TimerSpec(duration_seconds=1), timeout_seconds=20)
    assert stores[0].get_latest(tenant_id=RUN.tenant_id, run_id=RUN.run_id, task_id=WAIT_CHECKPOINT_TASK) is None
