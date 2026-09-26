"""Bounded executable assertions; intentionally avoids global synthetic pytest fixtures."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import replace

from bpmn_container import fixtures
from bpmn_container.artifacts import artifact_case
from bpmn_container.extensions import catch_case, region_case
from bpmn_container.hub import AcceptanceHub
from bpmn_container.public_api import PublicScenario, public_auth_case, public_scenario
from bpmn_container.result_boundary import result_binding_case

LIMIT_SECONDS = 25
TERMINAL = {"completed", "failed", "cancelled"}


def compile_request(xml, name, input_data=None):
    from agent.services.native_graph_orchestration_service import NativeGraphRequest
    from agent.services.workflow_runtime.execution_plan import WorkflowRequestExecutionPlanAdapter
    from agent.visual_process.blueprint_mapper import graph_to_workflow_request
    from agent.visual_process.bpmn_adapter import import_bpmn_xml

    workflow = graph_to_workflow_request(
        import_bpmn_xml(xml).graph, policy_scope={"source": "synthetic-container-acceptance"}
    )
    workflow = replace(
        workflow,
        metadata={
            **workflow.metadata,
            "project_id": "synthetic-bpmn-project",
            "execution_budget": {
                "max_attempts": 1,
                "timeout_seconds": 120.0,
                "max_tokens": 1000,
                "max_cost_micros": 1000,
            },
        },
    )
    plan = WorkflowRequestExecutionPlanAdapter.adapt(
        workflow, tenant_id="bpmn-container-tenant", policy_version="bpmn-core-test-policy-v1"
    )
    assert not plan.validate(), plan.validate()
    return NativeGraphRequest(
        plan,
        "bpmn-test-" + name,
        "control-" + name,
        input_data=input_data or {},
        tenant_parallel_limit=2,
        worker_parallel_limit=2,
    )


def advance(hub, request, result, *, dispatch=True, observer=None):
    deadline = time.monotonic() + LIMIT_SECONDS
    for _ in range(250):
        if result.status in TERMINAL or result.status == "waiting_for_approval":
            return result
        if time.monotonic() > deadline:
            break
        if dispatch:
            hub.dispatch_ready(request.run_id)
        hub.collect()
        result = hub.orchestrator.advance(request)
        if observer:
            observer(result)
        time.sleep(0.025)
    raise AssertionError(f"bounded_run_timeout:{request.run_id}:{result.status}:{result.reason_code}")


def event_summary(hub, request):
    return [
        {"sequence": event.sequence, "event_type": event.event_type, "step_id": event.step_id, "payload": event.payload}
        for event in hub.orchestrator.stream(request)
    ]


def evidence(hub, request, result):
    # Workflow/run keys are runtime identities only. No SRC_ or RUN_ is minted.
    result.checkpoint.verify(
        key_ring=hub.keys,
        tenant_id=request.plan.tenant_id,
        workflow_id=request.plan.workflow_id,
        run_id=request.run_id,
        task_id=request.control_task_id,
        plan_hash=request.plan.plan_hash,
        policy_version=request.plan.policy_version,
    )
    return {
        "status": result.status,
        "reason_code": result.reason_code,
        "checkpoint_revision": result.checkpoint.revision,
        "tasks": hub.observations(request.run_id),
        "events": event_summary(hub, request),
    }


def xor_case(hub, name, approved, default=False):
    request = compile_request(fixtures.xor(default=default), name, {"approved": approved})
    hub.active_request = request
    result = advance(hub, request, hub.orchestrator.start(request))
    assert result.status == "completed", result.reason_code
    expected = "yes_task" if approved else "no_task"
    observed = evidence(hub, request, result)
    assert [task["node"] for task in observed["tasks"]] == [expected]
    assert any(
        event["step_id"] == "choose" and event["payload"].get("selected_edge") == ("yes" if approved else "no")
        for event in observed["events"]
    )
    assert all(item["allowed"] for item in hub.authorizations if item["run_id"] == request.run_id)
    assert any(item["run_id"] == request.run_id for item in hub.authorizations)
    # A terminal advance must not enqueue or execute a second task.
    assert hub.orchestrator.advance(request).status == "completed"
    assert len(hub.tasks_for(request.run_id)) == 1
    return observed


def parallel_case(hub):
    request = compile_request(fixtures.parallel(), "and-join")
    hub.active_request = request
    saw_pending_sibling = False

    def check_join(result):
        nonlocal saw_pending_sibling
        completed = set(result.completed_node_ids)
        if "fast" in completed and "slow" not in completed:
            saw_pending_sibling = True
            nodes = {
                task.worker_execution_context["native_node_command"]["node"]["node_id"]: task
                for task in hub.tasks_for(request.run_id)
            }
            assert "after" not in nodes
            assert "join" not in completed
            from bpmn_container.transport import WORKER_URL, request_json

            request_json(
                WORKER_URL + "/release", token=os.environ["BPMN_DISPATCH_TOKEN"], payload={"task_id": nodes["slow"].id}
            )

    result = advance(hub, request, hub.orchestrator.start(request), observer=check_join)
    assert result.status == "completed", result.reason_code
    assert saw_pending_sibling, "AND test did not observe a pending sibling"
    observed = evidence(hub, request, result)
    tasks = {task["node"]: task for task in observed["tasks"]}
    assert set(tasks) == {"fast", "slow", "after"}
    assert tasks["after"]["result"]["started_at"] >= max(
        tasks[node]["result"]["finished_at"] for node in ("fast", "slow")
    )
    completed_events = {
        event["step_id"]: event["sequence"]
        for event in observed["events"]
        if event["event_type"] == "workflow.step.completed"
    }
    after = next(
        event["sequence"]
        for event in observed["events"]
        if event["event_type"] == "workflow.step.delegated" and event["step_id"] == "after"
    )
    assert after > max(completed_events["fast"], completed_events["slow"])
    return {**observed, "pending_sibling_observed": saw_pending_sibling}


def gate_case(hub, decision):
    from agent.services.workflow_runtime import SignedWorkflowCommand

    request = compile_request(fixtures.approval(), "policy-" + decision)
    hub.active_request = request
    waiting = advance(hub, request, hub.orchestrator.start(request))
    assert waiting.status == "waiting_for_approval"
    assert not hub.tasks_for(request.run_id), "user task escaped its gate"
    if decision == "blocked":
        # An explicit absent auto-approval policy returns a bounded blocked result.
        return {**evidence(hub, request, waiting), "headless_outcome": "blocked_by_policy"}
    checkpoint = waiting.checkpoint
    command = SignedWorkflowCommand.issue(
        key_ring=hub.keys,
        command_type=decision,
        tenant_id=checkpoint.tenant_id,
        workflow_id=checkpoint.workflow_id,
        run_id=checkpoint.run_id,
        step_id="review",
        checkpoint_id=checkpoint.checkpoint_id,
        expected_revision=checkpoint.revision,
        plan_hash=checkpoint.plan_hash,
        policy_version=checkpoint.policy_version,
        actor_id="test-policy:explicit-" + decision,
        actor_roles=("operator",),
        payload={"policy_fixture": "synthetic-headless-" + decision},
        now=time.time(),
    )
    # Production signature/revision validation must reject this before any Task exists.
    try:
        hub.orchestrator.resume(request, command=replace(command, actor_id="tampered"))
    except Exception as exc:
        assert "signature" in str(exc), str(exc)
    else:
        raise AssertionError("tampered policy decision accepted")
    assert not hub.tasks_for(request.run_id)
    result = advance(hub, request, hub.orchestrator.resume(request, command=command))
    assert result.status == ("completed" if decision == "approve" else "failed"), result.reason_code
    observed = evidence(hub, request, result)
    assert [task["node"] for task in observed["tasks"]] == (["review"] if decision == "approve" else [])
    expected = "workflow.approval.granted" if decision == "approve" else "workflow.approval.rejected"
    assert any(event["event_type"] == expected for event in observed["events"])
    return observed


def assignment_case(hub):
    from bpmn_container.transport import HUB_URL, WORKER_ID, WORKER_URL

    from agent.services.workflow_worker_assignment_runtime import bind_dispatched_workflow_task
    from ananta_contracts.workflow_worker_gateway import WORKFLOW_WORKER_COMMAND_SCHEMA

    request = compile_request(fixtures.xor(), "assignment-fence", {"approved": True})
    hub.active_request = request
    hub.orchestrator.start(request)
    for _ in range(10):
        tasks = hub.tasks_for(request.run_id)
        if tasks:
            break
        hub.orchestrator.advance(request)
    assert len(tasks) == 1
    task = tasks[0]
    command = task.worker_execution_context["native_node_command"]
    body = {
        "schema": WORKFLOW_WORKER_COMMAND_SCHEMA,
        "command": "authorize_execution",
        "adapter_kind": "native",
        "attempt_id": command["attempt_id"],
        "fencing_token": command["fencing_token"],
        "binding": {
            "tenant_id": command["tenant_id"],
            "workflow_id": command["workflow_id"],
            "run_id": command["run_id"],
            "step_id": command["node"]["node_id"],
            "plan_hash": command["plan_hash"],
            "policy_version": command["policy_version"],
            "authorization_envelope": command["authorization"],
        },
    }
    denials = []

    def denied(payload, identity, reason):
        req = urllib.request.Request(
            HUB_URL + "/api/internal/workflow-runtime/worker-commands",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + os.environ["BPMN_WORKER_TOKEN"],
                "X-Ananta-Worker-ID": identity,
                "X-Ananta-Worker-URL": WORKER_URL,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                raise AssertionError(f"invalid assignment authorized: HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            with exc:
                data = json.loads(exc.read(65536))
            assert exc.code in {403, 409}, data
            assert data["reason_code"] == reason, data
            denials.append(reason)

    denied(body, WORKER_ID, "workflow_worker_authenticated_owner_mismatch")
    bind_dispatched_workflow_task(task=task, worker=hub.worker, config=hub.app.config)
    denied(body, "unassigned-worker", "workflow_worker_service_identity_mismatch")
    denied({**body, "fencing_token": command["fencing_token"] + 1}, WORKER_ID, "workflow_worker_fencing_mismatch")
    assert task.id not in hub.dispatched
    assert not (hub.tasks.get_by_id(task.id).verification_status or {}).get("native_node_result")
    return {
        "status": "denied_before_execution",
        "denials": denials,
        "worker_executed": False,
        "events": event_summary(hub, request),
    }


def failure_observation(hub):
    """Preserve actual task/result/event facts even if terminal acceptance fails."""
    if hub.public.active_workflow_id:
        return hub.public.observation()
    if not getattr(hub, "active_request", None):
        return {"status": "no_active_run"}
    request = hub.active_request
    tasks = []
    for task in hub.tasks_for(request.run_id):
        command = task.worker_execution_context["native_node_command"]
        node = command["node"]["node_id"]
        assignment = hub.assignments.get(tenant_id=request.plan.tenant_id, run_id=request.run_id, step_id=node)
        owner = hub.ownership.get(tenant_id=request.plan.tenant_id, run_id=request.run_id, step_id=node)
        result = (task.verification_status or {}).get("native_node_result", {})
        tasks.append(
            {
                "task_id": task.id,
                "node": node,
                "status": task.status,
                "task_kind": command["node"]["task_kind"],
                "assigned": assignment is not None,
                "ownership_status": owner.status if owner else None,
                "result_status": result.get("status"),
                "result_reason": result.get("reason_code"),
                "output": result.get("output_data"),
            }
        )
    checkpoint = hub.orchestrator.checkpoint(request)
    from agent.services.native_graph_models import NativeRunState

    return {
        "tasks": tasks,
        "events": event_summary(hub, request),
        "checkpoint_event_cursor": NativeRunState.from_workflow_state(checkpoint.state).event_sequence,
    }


def main() -> int:
    report = {
        "schema": "bpmn_container_acceptance.v1",
        "capability_scope": "core-bounded-extensions-plus-public-boundaries",
        "evidence_classification": "synthetic_test_technical_observation",
        "production_release_evidence": False,
        "hub_hostname": socket.gethostname(),
        "chain": "production_services_over_isolated_container_http",
        "test_adapters": [
            "HTTP bootstrap/intake",
            "bounded Hub dispatch driver",
            "deterministic execution handler",
            "explicit signed policy fixtures",
            "explicit synthetic runtime admission port",
            "public facade composition lookup and diagnostic observer",
        ],
        "not_covered": [
            "full_application_boot",
            "autopilot_dispatch_loop",
            "production_runtime_release_admission",
            "artifact_ingress",
            "container_restart_recovery",
            "optional_backends",
        ],
        "missing_production_contracts": {
            "native_artifact_ingress": (
                "BPMN artifact plans are denied before execution: "
                "no assignment-bound content upload/receipt admission; "
                "generic forwarded artifacts are metadata normalization only. Recovery artifact ingress requires "
                "a recovery-child contract and cannot authorize ordinary Native tasks."
            ),
            "native_result_ingress": (
                "Generic forwarding persists worker result fields before Native validates attempt/fence. "
                "A rejected result cannot complete the canonical step but already appears on the task row."
            ),
        },
        "cases": {},
    }
    hub = None
    try:
        hub = AcceptanceHub()
        cases = {
            "xor_true": lambda: xor_case(hub, "xor-true", True),
            "xor_false": lambda: xor_case(hub, "xor-false", False),
            "xor_default": lambda: xor_case(hub, "xor-default", False, default=True),
            "and_join": lambda: parallel_case(hub),
            "policy_approve": lambda: gate_case(hub, "approve"),
            "policy_reject": lambda: gate_case(hub, "reject"),
            "policy_blocked": lambda: gate_case(hub, "blocked"),
            "assignment_fence": lambda: assignment_case(hub),
            "result_attempt_binding": lambda: result_binding_case(hub, "attempt_id"),
            "result_fence_binding": lambda: result_binding_case(hub, "fencing_token"),
            "artifact_unadmitted_output_denied": lambda: artifact_case(hub),
            "loop_sql_restart": lambda: region_case(hub, "loop"),
            "subprocess_sql_restart": lambda: region_case(hub, "subprocess"),
            "timer_sql_restart": lambda: catch_case(hub, "timer"),
            "message_sql_restart": lambda: catch_case(hub, "message"),
            "public_auth_and_release_denial": lambda: public_auth_case(hub),
            "public_graph_start": lambda: public_scenario(
                hub, PublicScenario("graph", fixtures.service(), ("work",)), replay_start=False, graph_start=True
            ),
            "public_start_replay": lambda: public_scenario(
                hub, PublicScenario("service", fixtures.service(), ("work",))
            ),
            "public_sql_recovery": lambda: public_scenario(
                hub, PublicScenario("recovery", fixtures.service(), ("work",)), rebuild=True
            ),
            "public_graph_start_replay": lambda: public_scenario(
                hub, PublicScenario("graph-replay", fixtures.service(), ("work",)), graph_start=True
            ),
        }
        if hub.browser.enabled:
            cases["authenticated_browser_editor"] = hub.browser.run
        requested = json.loads(os.environ.get("BPMN_CASES", "[]"))
        if requested:
            unknown = set(requested) - set(cases)
            if unknown:
                raise ValueError("unknown_bpmn_cases:" + ",".join(sorted(unknown)))
            report["case_selection"] = requested
            report["partial_case_selection"] = True
            cases = {name: case for name, case in cases.items() if name in requested}
        else:
            report["partial_case_selection"] = False
        with hub.app.app_context():
            for name, case in cases.items():
                hub.active_request = None
                hub.public.active_workflow_id = None
                started = time.monotonic()
                try:
                    observed = case()
                    report["cases"][name] = {
                        "passed": True,
                        **observed,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                    print(f"PASS {name}", flush=True)
                except Exception as exc:
                    report["cases"][name] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
                    traceback.print_exc()
                    try:
                        report["cases"][name]["observation"] = failure_observation(hub)
                    except Exception as diagnostic:
                        report["cases"][name]["diagnostic_error"] = str(diagnostic)
                    # Cases have separate run IDs; failed runs are never resumed or
                    # promoted. Drain bounded HTTP work before the next case.
                    deadline = time.monotonic() + 12
                    while hub.pending and time.monotonic() < deadline:
                        hub.collect()
                        time.sleep(0.025)
        report["passed"] = len(report["cases"]) == len(cases) and all(
            case["passed"] for case in report["cases"].values()
        )
    except Exception as exc:
        traceback.print_exc()
        report.update(passed=False, bootstrap_error=f"{type(exc).__name__}: {exc}")
    finally:
        if hub is not None:
            report["public_api_diagnostics"] = hub.public.failures
            if hub.browser.report is not None:
                report["browser_observation"] = hub.browser.report
            if not hub.browser.enabled:
                report["not_covered"].append("authenticated_browser_editor_not_requested")
            hub.close()
        print("BPMN_CONTAINER_REPORT=" + json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
