"""Real SQL restart and separate Worker checks for the bounded extensions."""

import time

from bpmn_container import fixtures


def rebuild(hub):
    from agent.services.native_graph_orchestration_service import NativeGraphOrchestrator
    from agent.services.native_graph_production_composition import HubGovernedNativeControlPolicy
    from agent.services.workflow_control_persistence import SQLAlchemyWorkflowCommandReplayNonceStore
    from agent.services.workflow_runtime import SQLAlchemyCheckpointStore, WorkflowCommandVerifier

    hub.orchestrator = NativeGraphOrchestrator(
        queue=hub.queue,
        checkpoints=SQLAlchemyCheckpointStore(hub.engine),
        events=hub.events,
        ownership=hub.ownership,
        ledger=hub.ledger,
        key_ring=hub.keys,
        command_verifier=WorkflowCommandVerifier(hub.keys, SQLAlchemyWorkflowCommandReplayNonceStore(hub.engine)),
        policy=HubGovernedNativeControlPolicy(),
        authorization_grants=hub.grants,
    )


def region_case(hub, kind):
    from bpmn_container.acceptance import advance, compile_request, evidence

    request = compile_request(getattr(fixtures, kind)(), kind, {"value": 0, "private_note": "must-not-delegate"})
    hub.active_request = request
    result = hub.orchestrator.start(request)
    for _ in range(30):
        if hub.tasks_for(request.run_id):
            break
        result = hub.orchestrator.advance(request)
    assert hub.tasks_for(request.run_id), "region_did_not_delegate"
    rebuild(hub)  # No Python Native state is retained; tasks/waits/grants remain SQL-owned.
    result = advance(hub, request, hub.orchestrator.advance(request))
    assert result.status == "completed", result.reason_code
    expected = [1, 2, 3] if kind == "loop" else [1]
    observed = evidence(hub, request, result)
    assert sorted(task["result"]["value"] for task in observed["tasks"]) == expected
    tasks = hub.tasks_for(request.run_id)
    assert len({task.worker_execution_context["native_node_command"]["node"]["node_id"] for task in tasks}) == len(
        expected
    )
    for task in tasks:
        command = task.worker_execution_context["native_node_command"]
        assert set(command["input_data"]["workflow_input"]) == {"value"}
        assert command["input_data"]["dependency_results"] == {}
    assert hub.orchestrator.advance(request).status == "completed"
    assert len(hub.tasks_for(request.run_id)) == len(expected)
    return {**observed, "sql_recomposition": True, "explicit_projection_verified": True}


def catch_case(hub, kind):
    from bpmn_container.acceptance import advance, compile_request, evidence

    from agent.services.workflow_runtime import SignedWorkflowCommand

    request = compile_request(fixtures.catch(kind), kind)
    hub.active_request = request
    waiting = hub.orchestrator.start(request)
    for _ in range(8):
        intent = waiting.checkpoint.state.runtime_metadata.get("bpmn_waits", {}).get("catch", {})
        if intent.get("wakeup_id"):
            break
        waiting = hub.orchestrator.advance(request)
    assert intent.get("wakeup_id"), "catch_not_armed"
    assert not hub.tasks_for(request.run_id), "successor_delegated_before_catch"
    rebuild(hub)
    if kind == "message":
        checkpoint = waiting.checkpoint
        definition = next(node for node in request.plan.nodes if node.node_id == "catch").metadata["bpmn_wait"]
        now = time.time()
        command = SignedWorkflowCommand.issue(
            key_ring=hub.keys,
            command_type="bpmn_message",
            tenant_id=checkpoint.tenant_id,
            workflow_id=checkpoint.workflow_id,
            run_id=checkpoint.run_id,
            step_id="catch",
            checkpoint_id=checkpoint.checkpoint_id,
            expected_revision=checkpoint.revision,
            plan_hash=checkpoint.plan_hash,
            policy_version=checkpoint.policy_version,
            actor_id="synthetic-message-policy",
            actor_roles=("operator",),
            now=now,
            payload={
                "activation_id": intent["activation_id"],
                "message_id": "synthetic-order-1",
                "name": definition["name"],
                "correlation_key": definition["correlation_key"],
                "schema_id": definition["schema_id"],
                "sent_at": now,
                "expires_at": now + 15,
                "payload": {"value": 7},
            },
        )
        hub.orchestrator.resume(request, command=command)
        rebuild(hub)
    result = advance(hub, request, hub.orchestrator.advance(request))
    assert result.status == "completed", result.reason_code
    observed = evidence(hub, request, result)
    assert [task["node"] for task in observed["tasks"]] == ["work"]
    assert len([event for event in observed["events"] if event["event_type"] == "workflow.bpmn.catch.consumed"]) == 1
    assert hub.orchestrator.advance(request).status == "completed"
    assert len(hub.tasks_for(request.run_id)) == 1
    return {**observed, "sql_recomposition": True, "wait_applied_before_dispatch": True}
