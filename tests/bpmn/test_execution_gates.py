"""Signed, policy-bound test approvals; never interactive or production evidence."""

import pytest

from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.workflow_runtime_selection_composition import _candidate
from tests.bpmn.test_execution_admission import diagram
from tests.bpmn.test_execution_recovery import advance_bounded
from tests.bpmn.test_execution_routing import compile_request, execution_plan
from tests.test_native_graph_runtime import runtime, signed_control


@pytest.mark.parametrize("approve", [True, False])
def test_user_task_delegates_only_after_signed_hub_approval(approve):
    xml = diagram(
        '<startEvent id="s"/><userTask id="review"/><endEvent id="end"/>'
        '<sequenceFlow id="a" sourceRef="s" targetRef="review"/>'
        '<sequenceFlow id="b" sourceRef="review" targetRef="end"/>'
    )
    hub, queue, handler, keys, *_ = runtime()
    request = NativeGraphRequest(execution_plan(compile_request(xml)), "gate-test", "control")
    waiting = advance_bounded(hub, request, hub.start(request))
    assert waiting.status == "waiting_for_approval"
    assert queue.submissions == []
    command = signed_control(
        keys=keys,
        checkpoint=waiting.checkpoint,
        command_type="approve" if approve else "reject",
        step_id="review",
        command_id="test-policy-decision",
    )
    result = hub.resume(request, command=command)
    result = advance_bounded(hub, request, result)
    if approve:
        assert result.status == "completed"
        assert handler.calls == ["review"]
    else:
        assert result.status in {"failed", "cancelled"}
        assert handler.calls == []


def test_bpmn_runtime_is_opt_in_and_no_other_runtime_claims_support(monkeypatch):
    monkeypatch.delenv("ANANTA_BPMN_EXECUTION_ENABLED", raising=False)
    assert "bpmn_control_v1" not in _candidate("ananta-native", native_production=True).capabilities
    monkeypatch.setenv("ANANTA_BPMN_EXECUTION_ENABLED", "true")
    assert "bpmn_control_v1" in _candidate("ananta-native", native_production=True).capabilities
    assert "bpmn_control_v1" not in _candidate("ananta-native", native_production=False).capabilities
    assert "bpmn_control_v1" not in _candidate("temporal", native_production=True).capabilities
