"""Hostile result ingress fixtures, after real cross-container execution."""

from __future__ import annotations

import copy
import time


def result_binding_case(hub, field):
    from bpmn_container.acceptance import compile_request, event_summary
    from bpmn_container.fixtures import service

    request = compile_request(service(), "result-" + field)
    hub.active_request = request
    hub.orchestrator.start(request)
    for _ in range(10):
        if hub.tasks_for(request.run_id):
            break
        hub.orchestrator.advance(request)
    assert len(hub.tasks_for(request.run_id)) == 1
    hub.dispatch_ready(request.run_id)
    injected = False

    def tamper(response):
        nonlocal injected
        response = copy.deepcopy(response)
        raw = response["workflow_adapter_verification"]["workflow_adapter_task_result"]["adapter_result"][
            "verification"
        ]["native_node_result"]
        assert raw["status"] == "completed", raw
        raw[field] = raw[field] + 1 if field == "fencing_token" else "unassigned-attempt"
        injected = True
        return response

    deadline = time.monotonic() + 15
    while hub.pending and time.monotonic() < deadline:
        hub.collect(transform_result=tamper)
        time.sleep(0.025)
    assert injected and not hub.pending
    try:
        hub.orchestrator.advance(request)
    except ValueError as exc:
        assert str(exc) == "native_node_result_binding_mismatch", str(exc)
    else:
        raise AssertionError("mismatched_worker_result_completed_workflow")
    events = event_summary(hub, request)
    assert not any(event["event_type"] == "workflow.step.completed" and event["step_id"] == "work" for event in events)
    owner = hub.ownership.get(tenant_id=request.plan.tenant_id, run_id=request.run_id, step_id="work")
    assert not owner.result_ack_key
    return {
        "status": "rejected_by_native_result_binding",
        "field": field,
        "fault_injection": "mutated_http_result_after_real_worker_execution",
        "canonical_step_completed": False,
        "ownership_acknowledged": False,
        "ingress_limitation": "generic_forwarder_persists_untrusted_task_result_before_native_validation",
    }
