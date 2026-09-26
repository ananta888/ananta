"""Synthetic signed Native/wait integration observations, never release evidence."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent.services.bpmn_event_wait_contracts import EventWaitError
from agent.services.bpmn_event_wait_store import CheckpointEventWaitStore
from agent.services.bpmn_native_event_runtime import BpmnNativeEventRuntime, wait_run_binding
from agent.services.bpmn_run_lease import BpmnRunLeaseService
from agent.services.native_graph_models import NativeGraphRequest, NativeRunState
from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.commands import SignedWorkflowCommand
from agent.services.workflow_runtime.errors import OptimisticConcurrencyError, SignatureValidationError
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.persistence import SQLiteCheckpointStore
from agent.services.workflow_runtime.security import HmacKeyRing, SignedCheckpoint
from agent.visual_process.bpmn_event_definitions import (
    BpmnEventDefinitionError,
    parse_event_definition,
    parse_timer_date,
    parse_timer_duration,
    validate_event_definition,
    validate_message_payload,
)
from agent.visual_process.bpmn_execution_support import parse_bpmn

SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}
TIMER = {"kind": "timer", "timer": {"duration_seconds": 5.0}, "timeout_seconds": 20.0}
MESSAGE = {
    "kind": "message",
    "name": "order.received",
    "correlation_key": "order-42",
    "schema_id": sha256_json(SCHEMA),
    "payload_schema": SCHEMA,
    "timeout_seconds": 20.0,
}


def event_xml(event, *, options=None, declaration="", element="intermediateCatchEvent"):
    extension = (
        ""
        if options is None
        else (
            '<extensionElements><metadata xmlns="https://ananta.local/bpmn">'
            + json.dumps({"bpmn_wait": options})
            + "</metadata></extensionElements>"
        )
    )
    xml = (
        '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" targetNamespace="urn:test">'
        + declaration
        + f'<process id="process"><{element} id="catch">'
        + extension
        + event
        + f"</{element}></process></definitions>"
    )
    root = parse_bpmn(xml)
    return parse_event_definition(root.find("{http://www.omg.org/spec/BPMN/20100524/MODEL}process")[0], root)


@pytest.mark.parametrize("value,expected", [("PT0S", 0), ("PT1H2M3.5S", 3723.5), ("P0DT1M", 60)])
def test_iso_timer_fixed_units(value, expected):
    assert parse_timer_duration(value) == expected


@pytest.mark.parametrize(
    "value",
    ["P", "PT", "P1DT", "P1M", "P1Y", "P1W", "R3/PT1S", "PT-1S", "PTnanS", "P1D", "PT86400S", " PT1S", "PT1e3S"],
)
def test_unsupported_or_unbounded_timer_durations(value):
    with pytest.raises(BpmnEventDefinitionError):
        parse_timer_duration(value)


def test_timezone_dates_are_unambiguous_and_normalized():
    assert parse_timer_date("2026-09-24T12:30:00+02:00") == parse_timer_date("2026-09-24T10:30:00Z")


@pytest.mark.parametrize(
    "value",
    ["2026-09-24", "2026-09-24T12:30:00", "2026-09-24 12:30:00Z", "2026-09-24T12:30:00+01:99", "2026-02-30T12:30:00Z"],
)
def test_invalid_dates(value):
    with pytest.raises(BpmnEventDefinitionError):
        parse_timer_date(value)


def test_timer_xml_lowers_to_closed_definition():
    assert (
        event_xml(
            "<timerEventDefinition><timeDuration>PT5S</timeDuration></timerEventDefinition>",
            options={"timeout_seconds": 20},
        )
        == TIMER
    )


@pytest.mark.parametrize(
    "event,element",
    [
        ("<timerEventDefinition><timeCycle>R3/PT1S</timeCycle></timerEventDefinition>", "intermediateCatchEvent"),
        ("<timerEventDefinition><timeDuration>PT1S</timeDuration></timerEventDefinition>", "startEvent"),
        ("<timerEventDefinition><timeDuration>PT1S</timeDuration></timerEventDefinition>", "boundaryEvent"),
        (
            "<timerEventDefinition><timeDuration>PT1S</timeDuration><timeDate>2026-09-24T10:30:00Z</timeDate></timerEventDefinition>",
            "intermediateCatchEvent",
        ),
        ("<signalEventDefinition/>", "intermediateCatchEvent"),
        (
            '<timerEventDefinition><timeDuration language="javascript">PT1S</timeDuration></timerEventDefinition>',
            "intermediateCatchEvent",
        ),
    ],
)
def test_event_xml_rejects_unsupported_semantics(event, element):
    with pytest.raises(BpmnEventDefinitionError):
        event_xml(event, element=element)


def test_message_xml_pins_explicit_declaration_and_closed_local_schema():
    assert (
        event_xml(
            '<messageEventDefinition messageRef="order"/>',
            options={"correlation_key": "order-42", "payload_schema": SCHEMA, "timeout_seconds": 20},
            declaration='<message id="order" name="order.received"/>',
        )
        == MESSAGE
    )


@pytest.mark.parametrize(
    "declaration", ["", '<message id="order"/>', '<message id="order" name="order.received" itemRef="external"/>']
)
def test_message_xml_requires_a_named_closed_declaration(declaration):
    with pytest.raises(BpmnEventDefinitionError):
        event_xml(
            '<messageEventDefinition messageRef="order"/>',
            options={"correlation_key": "order-42", "payload_schema": SCHEMA},
            declaration=declaration,
        )


def test_compiled_definition_cannot_change_schema_or_add_authority():
    for changes in ({"schema_id": "changed"}, {"tenant_id": "forged"}, {"early_messages": True}):
        with pytest.raises(BpmnEventDefinitionError):
            validate_event_definition({**MESSAGE, **changes})


def test_closed_scalar_schema_supports_optional_fields_without_type_coercion():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"type": "integer"}, "label": {"type": "string", "maxLength": 3}},
    }
    validate_message_payload(schema, {"value": 3})
    validate_message_payload(schema, {"value": 3, "label": "yes"})
    for payload in ({"value": "3"}, {"value": 3, "label": "long"}):
        with pytest.raises(BpmnEventDefinitionError):
            validate_message_payload(schema, payload)


@pytest.mark.parametrize(
    "schema",
    [
        {**SCHEMA, "$ref": "https://untrusted.invalid/schema"},
        {**SCHEMA, "additionalProperties": True},
        {**SCHEMA, "properties": {"value": {"type": "object"}}},
        {**SCHEMA, "properties": {"value": {"type": "string"}}},
    ],
)
def test_message_schema_rejects_external_open_nested_or_unbounded_shapes(schema):
    with pytest.raises(BpmnEventDefinitionError):
        validate_event_definition({**MESSAGE, "payload_schema": schema, "schema_id": sha256_json(schema)})


@pytest.mark.parametrize("payload", [{"value": True}, {"value": 1, "extra": 2}, {}, {"value": [1]}])
def test_message_scalar_payload_type_is_exact(payload):
    with pytest.raises(BpmnEventDefinitionError):
        validate_message_payload(SCHEMA, payload)


class Harness:
    """Real signed SQLite CAS and real parent control leases, with a fixed clock."""

    def __init__(self, path, definition=TIMER):
        self.path = path
        self.now = 100.0
        self.keys = HmacKeyRing({"synthetic": b"synthetic-signing-material-only-32"}, active_key_id="synthetic")
        self.node = ExecutionNode("catch", node_type="bpmn_wait", metadata={"bpmn_wait": definition})
        self.plan = ExecutionPlan(
            "tenant-a",
            "plan-a",
            "workflow-a",
            "policy-a",
            (self.node,),
            metadata={"project_id": "project-a", "bpmn_definition_hash": "d" * 64},
        )
        self.request = NativeGraphRequest(self.plan, "synthetic-run", "native-control")
        self.state = NativeRunState(bpmn_started_at=100.0, bpmn_deadline_at=200.0)
        self.revision = 0
        self.fault = False
        self.persisted_applied = []
        self._open()

    def _open(self):
        self.checkpoints = SQLiteCheckpointStore(self.path)
        self.store = CheckpointEventWaitStore(self.checkpoints, self.keys)
        self.adapter = BpmnNativeEventRuntime(store=self.store, clock=lambda: self.now)
        self.leases = BpmnRunLeaseService(checkpoints=self.checkpoints, keys=self.keys, clock=lambda: self.now)

    def restart(self):
        self.checkpoints.close()
        self._open()
        saved = self.checkpoints.get_latest(
            tenant_id=self.plan.tenant_id, run_id=self.request.run_id, task_id=self.request.control_task_id
        )
        saved.verify(
            key_ring=self.keys,
            tenant_id=self.plan.tenant_id,
            workflow_id=self.plan.workflow_id,
            run_id=self.request.run_id,
            task_id=self.request.control_task_id,
            plan_hash=self.plan.plan_hash,
            policy_version=self.plan.policy_version,
        )
        self.state = NativeRunState.from_workflow_state(saved.state)
        self.revision = saved.revision

    def persist(self, lease):
        lease.ensure_valid()
        if self.fault and self.state.bpmn_applied_wakeups:
            raise RuntimeError("synthetic-native-checkpoint-crash")
        checkpoint = SignedCheckpoint.issue(
            key_ring=self.keys,
            tenant_id=self.plan.tenant_id,
            workflow_id=self.plan.workflow_id,
            run_id=self.request.run_id,
            task_id=self.request.control_task_id,
            plan_hash=self.plan.plan_hash,
            policy_version=self.plan.policy_version,
            runtime_id="ananta-native",
            runtime_version="1.0.0",
            state=self.state.to_workflow_state(secret_refs=()),
            revision=self.revision + 1,
            fencing_token=lease.fencing_token,
            now=self.now,
        )
        self.checkpoints.save_fenced(checkpoint, expected_revision=self.revision, lease=lease)
        self.revision += 1
        self.persisted_applied.append(set(self.state.bpmn_applied_wakeups))

    def invoke(self, method, **extra):
        with self.leases.acquire(self.request) as lease:
            self.state.control_lease = lease
            return getattr(self.adapter, method)(
                plan=self.plan,
                request=self.request,
                state=self.state,
                fencing_token=lease.fencing_token,
                guard=lease.ensure_valid,
                persist=lambda: self.persist(lease),
                **extra,
            )

    def command(self, **overrides):
        payload = {
            "activation_id": self.state.bpmn_waits["catch"]["activation_id"],
            "message_id": "message-1",
            "name": MESSAGE["name"],
            "correlation_key": MESSAGE["correlation_key"],
            "schema_id": MESSAGE["schema_id"],
            "sent_at": 100.0,
            "expires_at": 115.0,
            "payload": {"value": 7},
        }
        payload.update(overrides)
        checkpoint = self.checkpoints.get_latest(
            tenant_id=self.plan.tenant_id, run_id=self.request.run_id, task_id=self.request.control_task_id
        )
        command = SignedWorkflowCommand.issue(
            key_ring=self.keys,
            command_type="bpmn_message",
            tenant_id=self.plan.tenant_id,
            workflow_id=self.plan.workflow_id,
            run_id=self.request.run_id,
            step_id="catch",
            checkpoint_id=checkpoint.checkpoint_id,
            expected_revision=checkpoint.revision,
            plan_hash=self.plan.plan_hash,
            policy_version=self.plan.policy_version,
            actor_id="verified-test-actor",
            actor_roles=("operator",),
            payload=payload,
            now=self.now,
            ttl_seconds=100,
        )
        command.verify(
            key_ring=self.keys,
            tenant_id=self.plan.tenant_id,
            workflow_id=self.plan.workflow_id,
            run_id=self.request.run_id,
            step_id="catch",
            checkpoint_id=checkpoint.checkpoint_id,
            expected_revision=checkpoint.revision,
            plan_hash=self.plan.plan_hash,
            policy_version=self.plan.policy_version,
            now=self.now,
        )
        return command


@pytest.fixture
def harness(tmp_path):
    opened = []

    def create(definition=TIMER):
        result = Harness(tmp_path / f"events-{len(opened)}.sqlite", definition)
        opened.append(result)
        return result

    yield create
    for value in opened:
        value.checkpoints.close()


def test_timer_restarts_without_reset_and_persists_application_before_return(harness):
    hub = harness()
    armed = hub.invoke("arm", node=hub.node)
    assert armed.due_at == 105
    assert hub.invoke("reconcile") == ()
    hub.now = 104
    hub.restart()
    assert hub.invoke("arm", node=hub.node) == armed
    hub.now = 105
    (receipt,) = hub.invoke("reconcile")
    assert receipt.wakeup_id in hub.persisted_applied[-1]
    assert hub.state.completed == {"catch"}
    hub.restart()
    assert hub.invoke("reconcile") == (receipt,)
    assert len(hub.state.bpmn_applied_wakeups) == 1


def test_crash_after_consume_recovers_receipt_even_after_original_wait_expiry(harness):
    hub = harness()
    hub.invoke("arm", node=hub.node)
    hub.now = 105
    hub.fault = True
    with pytest.raises(RuntimeError, match="checkpoint-crash"):
        hub.invoke("reconcile")
    assert not hub.state.completed
    assert not hub.state.bpmn_applied_wakeups
    hub.fault = False
    hub.now = 125
    hub.restart()
    (receipt,) = hub.invoke("reconcile")
    assert receipt.consumed_at == 105
    assert hub.state.completed == {"catch"}


def test_authenticated_message_duplicate_never_reapplies_receipt(harness):
    hub = harness(MESSAGE)
    hub.invoke("arm", node=hub.node)
    command = hub.command()
    first = hub.invoke("deliver_message", command=command)
    assert first.status == "matched"
    assert hub.invoke("deliver_message", command=command) == first
    (receipt,) = hub.invoke("reconcile")
    assert hub.state.node_results["catch"]["payload"] == {"value": 7}
    hub.restart()
    assert hub.invoke("deliver_message", command=command) == first
    assert hub.invoke("reconcile") == (receipt,)
    with pytest.raises(ValueError, match="message_id_conflict"):
        hub.invoke("deliver_message", command=hub.command(payload={"value": 8}))


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"tenant_id": "forged"}, "payload_invalid"),
        ({"actor_id": "forged"}, "payload_invalid"),
        ({"activation_id": "other"}, "target_binding_mismatch"),
        ({"correlation_key": "other"}, "target_binding_mismatch"),
        ({"schema_id": "other"}, "target_binding_mismatch"),
        ({"payload": {"value": True}}, "payload_schema_mismatch"),
    ],
)
def test_signed_message_does_not_accept_payload_authority(harness, changes, reason):
    hub = harness(MESSAGE)
    hub.invoke("arm", node=hub.node)
    before = hub.store.load(wait_run_binding(hub.plan, hub.request))
    with pytest.raises(ValueError, match=reason):
        hub.invoke("deliver_message", command=hub.command(**changes))
    after = hub.store.load(wait_run_binding(hub.plan, hub.request))
    assert before == after


def test_early_delivery_and_cancelled_run_fail_closed(harness):
    hub = harness(MESSAGE)
    hub.invoke("arm", node=hub.node)
    command = hub.command()
    intent = hub.state.bpmn_waits.pop("catch")
    with pytest.raises(EventWaitError, match="early_message_denied"):
        hub.invoke("deliver_message", command=command)
    hub.state.bpmn_waits["catch"] = intent
    hub.invoke("cancel_run")
    with pytest.raises(EventWaitError, match="run_closed"):
        hub.invoke("deliver_message", command=command)
    with pytest.raises(EventWaitError, match="run_closed"):
        hub.invoke("reconcile")
    assert not hub.state.completed


def test_expired_timer_does_not_complete(harness):
    hub = harness()
    hub.invoke("arm", node=hub.node)
    hub.now = 120
    with pytest.raises(EventWaitError, match="expired"):
        hub.invoke("reconcile")
    assert not hub.state.completed


def test_cancel_tombstone_wins_over_unapplied_consumed_receipt_after_restart(harness):
    hub = harness()
    hub.invoke("arm", node=hub.node)
    hub.now = 105
    hub.fault = True
    with pytest.raises(RuntimeError, match="checkpoint-crash"):
        hub.invoke("reconcile")
    hub.fault = False
    hub.invoke("cancel_run")
    hub.restart()  # Native still says running, but the cancellation tombstone is durable.
    with pytest.raises(EventWaitError, match="run_closed"):
        hub.invoke("reconcile")
    assert not hub.state.completed


def test_stale_lease_cannot_mutate_before_successor_writes(harness):
    hub = harness()
    with hub.leases.acquire(hub.request) as lease:
        hub.now += 31
        with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
            hub.adapter.arm(
                plan=hub.plan,
                request=hub.request,
                state=hub.state,
                node=hub.node,
                fencing_token=lease.fencing_token,
                guard=lease.ensure_valid,
                persist=lambda: hub.persist(lease),
            )
        assert hub.store.load(wait_run_binding(hub.plan, hub.request)).revision == 0
        with hub.leases.acquire(hub.request) as successor:
            assert successor.fencing_token > lease.fencing_token
            with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
                lease.ensure_valid()


def test_guard_rechecks_after_wait_load_immediately_before_commit(harness):
    hub = harness()
    original = hub.checkpoints.get_latest

    def expire_after_load(**kwargs):
        value = original(**kwargs)
        if kwargs["task_id"] == "bpmn-event-waits:v1":
            hub.now += 31
        return value

    hub.checkpoints.get_latest = expire_after_load
    with pytest.raises(OptimisticConcurrencyError, match="lease_stale"):
        hub.invoke("arm", node=hub.node)
    assert original(tenant_id=hub.plan.tenant_id, run_id=hub.request.run_id, task_id="bpmn-event-waits:v1") is None
    assert not hub.state.completed


def test_wait_snapshot_signature_tampering_cannot_resume(harness):
    hub = harness()
    hub.invoke("arm", node=hub.node)
    from agent.services.bpmn_event_wait_store import WAIT_CHECKPOINT_TASK

    checkpoint = hub.checkpoints.get_latest(
        tenant_id=hub.plan.tenant_id, run_id=hub.request.run_id, task_id=WAIT_CHECKPOINT_TASK
    )
    original = hub.checkpoints.get_latest

    def tampered(**kwargs):
        return replace(checkpoint, signature="bad") if kwargs["task_id"] == WAIT_CHECKPOINT_TASK else original(**kwargs)

    hub.checkpoints.get_latest = tampered
    hub.now = 105
    with pytest.raises(SignatureValidationError):
        hub.invoke("reconcile")
    assert not hub.state.completed


@pytest.fixture
def native_harness(tmp_path):
    from tests.bpmn.completion_helpers import CompletionHarness

    value = CompletionHarness(tmp_path)
    try:
        yield value
    finally:
        value.close()


def native_wait_request(kind="timer"):
    from tests.bpmn.test_execution_admission import diagram
    from tests.bpmn.test_execution_routing import compile_request, execution_plan

    options = {"timeout_seconds": 20}
    declaration = ""
    if kind == "message":
        options.update(correlation_key=MESSAGE["correlation_key"], payload_schema=SCHEMA)
        declaration = '<message id="order" name="order.received"/>'
        event = '<messageEventDefinition messageRef="order"/>'
    else:
        event = "<timerEventDefinition><timeDuration>PT5S</timeDuration></timerEventDefinition>"
    extension = (
        '<extensionElements><metadata xmlns="https://ananta.local/bpmn">'
        + json.dumps({"bpmn_wait": options})
        + "</metadata></extensionElements>"
    )
    xml = diagram(
        '<startEvent id="start"/><intermediateCatchEvent id="catch">'
        + extension
        + event
        + '</intermediateCatchEvent><serviceTask id="after"/><endEvent id="end"/>'
        '<sequenceFlow id="begin" sourceRef="start" targetRef="catch"/>'
        '<sequenceFlow id="next" sourceRef="catch" targetRef="after"/>'
        '<sequenceFlow id="finish" sourceRef="after" targetRef="end"/>'
    )
    xml = xml.replace("<process ", declaration + "<process ", 1)
    plan = execution_plan(compile_request(xml))
    plan = replace(plan, metadata={**plan.metadata, "project_id": "synthetic-project"})
    return NativeGraphRequest(plan, "synthetic-native-events", "native-event-control")


def native_reach_wait(harness, request):
    result = harness.hub.start(request)
    for _ in range(8):
        if result.checkpoint.state.runtime_metadata.get("bpmn_waits", {}).get("catch", {}).get("wakeup_id"):
            assert result.status == "running"
            assert harness.queue.submissions == []
            return result
        assert result.status == "running", result.reason_code
        result = harness.hub.advance(request)
    pytest.fail("Intermediate catch did not arm within eight bounded Native ticks")


def test_actual_native_timer_never_delegates_before_applied_checkpoint(native_harness, monkeypatch):
    hub = native_harness
    request = native_wait_request()
    native_reach_wait(hub, request)
    submit = hub.queue.submit

    def checked_submit(command):
        checkpoint = hub.stores["checkpoints"].get_latest(
            tenant_id=request.plan.tenant_id, run_id=request.run_id, task_id=request.control_task_id
        )
        runtime = checkpoint.state.runtime_metadata
        assert runtime["bpmn_applied_wakeups"]
        assert "catch" in runtime["completed"]
        assert command.node.node_id == "after"
        return submit(command)

    monkeypatch.setattr(hub.queue, "submit", checked_submit)
    hub.now = 104
    hub.restart()
    hub.hub.advance(request)
    assert hub.queue.submissions == []
    hub.now = 105
    result = hub.finish(request)
    assert result.status == "completed", result.reason_code
    assert hub.handler.calls == ["after"]
    assert len([event for event in hub.hub.stream(request) if event.event_type == "workflow.bpmn.catch.consumed"]) == 1


def test_actual_native_signed_message_resumes_only_targeted_catch(native_harness):
    from tests.test_native_graph_runtime import signed_control

    hub = native_harness
    request = native_wait_request("message")
    waiting = native_reach_wait(hub, request)
    intent = waiting.checkpoint.state.runtime_metadata["bpmn_waits"]["catch"]
    command = signed_control(
        keys=hub.keys,
        checkpoint=waiting.checkpoint,
        command_type="bpmn_message",
        step_id="catch",
        command_id="synthetic-message-command",
        payload={
            "activation_id": intent["activation_id"],
            "message_id": "order-1",
            "name": MESSAGE["name"],
            "correlation_key": MESSAGE["correlation_key"],
            "schema_id": MESSAGE["schema_id"],
            "sent_at": 100.0,
            "expires_at": 115.0,
            "payload": {"value": 7},
        },
    )
    hub.hub.resume(request, command=command)
    hub.restart()
    result = hub.finish(request)
    assert result.status == "completed", result.reason_code
    assert hub.handler.calls == ["after"]
    assert result.checkpoint.state.business_data["node_results"]["catch"]["payload"] == {"value": 7}
    assert len(result.checkpoint.state.runtime_metadata["bpmn_applied_wakeups"]) == 1


@pytest.mark.parametrize("after", [False, True], ids=["before-checkpoint", "after-checkpoint"])
def test_actual_native_recovers_consume_application_crash(native_harness, monkeypatch, after):
    from tests.bpmn.completion_helpers import SimulatedHubCrash, crash_once

    hub = native_harness
    request = native_wait_request()
    native_reach_wait(hub, request)
    hub.now = 105
    crash_once(
        monkeypatch,
        hub.stores["checkpoints"],
        "save_fenced",
        after=after,
        predicate=lambda checkpoint, **kwargs: (
            checkpoint.task_id == request.control_task_id
            and bool(checkpoint.state.runtime_metadata.get("bpmn_applied_wakeups"))
        ),
    )
    with pytest.raises(SimulatedHubCrash):
        hub.hub.advance(request)
    assert hub.queue.submissions == []
    hub.now = 125  # A consumed receipt remains valid after the original catch deadline.
    hub.restart()
    result = hub.finish(request)
    assert result.status == "completed", result.reason_code
    assert hub.handler.calls == ["after"]
    assert len([event for event in hub.hub.stream(request) if event.event_type == "workflow.bpmn.catch.consumed"]) == 1


def test_native_wait_does_not_occupy_the_only_worker_slot(native_harness):
    from tests.bpmn.test_execution_admission import diagram
    from tests.bpmn.test_execution_routing import compile_request, execution_plan

    xml = diagram(
        '<startEvent id="start"/><parallelGateway id="split"/>'
        '<intermediateCatchEvent id="a_wait"><timerEventDefinition><timeDuration>PT5S</timeDuration>'
        '</timerEventDefinition></intermediateCatchEvent><serviceTask id="work"/>'
        '<parallelGateway id="join"/><endEvent id="end"/>'
        '<sequenceFlow id="begin" sourceRef="start" targetRef="split"/>'
        '<sequenceFlow id="timer" sourceRef="split" targetRef="a_wait"/>'
        '<sequenceFlow id="worker" sourceRef="split" targetRef="work"/>'
        '<sequenceFlow id="timer_done" sourceRef="a_wait" targetRef="join"/>'
        '<sequenceFlow id="worker_done" sourceRef="work" targetRef="join"/>'
        '<sequenceFlow id="finish" sourceRef="join" targetRef="end"/>'
    )
    plan = execution_plan(compile_request(xml))
    plan = replace(plan, metadata={**plan.metadata, "project_id": "synthetic-project"})
    request = NativeGraphRequest(
        plan, "synthetic-parallel-wait", "control", tenant_parallel_limit=1, worker_parallel_limit=1
    )
    hub = native_harness
    result = hub.hub.start(request)
    for _ in range(8):
        if hub.queue.submissions:
            break
        result = hub.hub.advance(request)
    assert hub.handler.calls == ["work"]
    assert "a_wait" not in result.completed_node_ids
    hub.now = 105
    assert hub.finish(request).status == "completed"


def test_event_capability_is_opt_in_and_native_only(monkeypatch):
    from agent.services.workflow_runtime_selection_composition import _candidate

    monkeypatch.delenv("ANANTA_BPMN_EXECUTION_ENABLED", raising=False)
    assert "bpmn_events_v1" not in _candidate("ananta-native", native_production=True).capabilities
    monkeypatch.setenv("ANANTA_BPMN_EXECUTION_ENABLED", "1")
    assert "bpmn_events_v1" in _candidate("ananta-native", native_production=True).capabilities
    assert "bpmn_events_v1" not in _candidate("ananta-native", native_production=False).capabilities
    assert "bpmn_events_v1" not in _candidate("temporal", native_production=True).capabilities


def test_native_cancellation_closes_waits_and_requires_a_new_run(native_harness):
    from tests.test_native_graph_runtime import signed_control

    hub = native_harness
    request = native_wait_request()
    waiting = native_reach_wait(hub, request)
    cancel = signed_control(
        keys=hub.keys,
        checkpoint=waiting.checkpoint,
        command_type="cancel",
        step_id="__workflow__",
        command_id="synthetic-cancel",
    )
    cancelled = hub.hub.resume(request, command=cancel)
    assert cancelled.status == "cancelled"
    hub.now = 105
    hub.restart()
    assert hub.hub.advance(request).status == "cancelled"
    assert hub.queue.submissions == []
    retry = signed_control(
        keys=hub.keys,
        checkpoint=cancelled.checkpoint,
        command_type="retry",
        step_id="__workflow__",
        command_id="synthetic-retry",
    )
    with pytest.raises(ValueError, match="closed_run_retry_denied"):
        hub.hub.resume(request, command=retry)


def test_native_plan_cannot_erase_or_forge_wait_admission():
    request = native_wait_request()
    plan = request.plan
    assert "bpmn_events_v1" in plan.capabilities
    assert not plan.validate()
    changed = replace(plan, capabilities=tuple(cap for cap in plan.capabilities if cap != "bpmn_events_v1"))
    assert "bpmn_events_capability_required" in {issue.code for issue in changed.validate()}
    for node in plan.nodes:
        if node.node_type != "bpmn_wait":
            continue
        disguised = replace(
            plan, nodes=tuple(replace(item, node_type="task") if item == node else item for item in plan.nodes)
        )
        assert "bpmn_wait_on_task" in {issue.code for issue in disguised.validate()}


def test_native_rejects_missing_event_project_before_run_events_or_delegation(native_harness):
    request = native_wait_request()
    plan = replace(
        request.plan,
        metadata={key: value for key, value in request.plan.metadata.items() if key != "project_id"},
    )
    request = replace(request, plan=plan)
    with pytest.raises(ValueError, match="bpmn_wait_project_binding_required"):
        native_harness.hub.start(request)
    assert native_harness.queue.submissions == []
    assert native_harness.hub.stream(request) == ()
