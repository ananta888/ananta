"""Compile/preflight/start must keep stable semantics and one audit identity."""

from dataclasses import replace

from agent.services.bpmn_workflow_preflight import workflow_start_plan
from agent.services.native_graph_models import NativeGraphRequest
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand
from tests.bpmn.test_execution_recovery import advance_bounded
from tests.bpmn.test_execution_routing import compile_request, execution_plan, xor_xml
from tests.test_native_graph_runtime import runtime
from worker.runtime.native_graph.composition import NativeHubExecutionScope


def test_repeated_xml_compilation_has_stable_request_and_plan_bindings():
    first, second = compile_request(xor_xml()), compile_request(xor_xml())
    assert first.to_dict() == second.to_dict()
    assert (
        workflow_start_plan(first, tenant_id="tenant").plan_hash
        == workflow_start_plan(second, tenant_id="tenant").plan_hash
    )


def test_custom_correlation_survives_hub_queue_wire_and_worker_audit_binding():
    hub, queue, *_ = runtime()
    request = NativeGraphRequest(
        execution_plan(compile_request(xor_xml())),
        "run-correlation",
        "control",
        correlation_id="trace-exact",
        input_data={"approved": True},
    )
    assert advance_bounded(hub, request, hub.start(request)).status == "completed"
    delegated = NativeNodeCommand.from_mapping(queue.submissions[0].to_dict())
    assert delegated.correlation_id == "trace-exact"
    assert NativeHubExecutionScope._binding(delegated).correlation_id == "trace-exact"
    assert NativeHubExecutionScope._binding(replace(delegated, correlation_id="")).correlation_id == request.run_id
