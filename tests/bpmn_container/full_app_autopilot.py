"""Full production Hub boot plus real bounded Native Autopilot dispatch."""

import os
import socket
import time

from bpmn_container.transport import WORKER_URL


def prepare_worker(app):
    from bpmn_container.registration import register_worker
    from flask import jsonify

    from agent.auth import check_registered_worker_auth
    from agent.repository import agent_repo
    from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring
    from agent.services.workflow_worker_service_auth import WORKFLOW_WORKER_COMMAND_SCOPE

    app.config["ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH"] = True
    register_worker(app, agent_repo)

    @app.get("/verification-keyring")
    @check_registered_worker_auth(scope=WORKFLOW_WORKER_COMMAND_SCOPE)
    def verification_keyring():
        return jsonify(get_workflow_authorization_key_ring().verification_mapping())


def exercise_dispatch(app, base, token):
    from bpmn_container import fixtures

    from agent.database import engine
    from agent.services.native_graph_orchestration_service import NativeGraphOrchestrator
    from agent.services.native_graph_production_composition import HubGovernedNativeControlPolicy
    from agent.services.native_graph_task_queue_adapter import build_native_graph_task_queue_adapter
    from agent.services.workflow_authorization_grant_service import SQLAlchemyWorkflowAuthorizationGrantService
    from agent.services.workflow_control_persistence import SQLAlchemyWorkflowCommandReplayNonceStore
    from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring
    from agent.services.workflow_runtime import (
        SQLAlchemyCheckpointStore,
        SQLAlchemyEventStore,
        SQLAlchemyExecutionOwnershipStore,
        SQLAlchemySideEffectLedger,
        WorkflowCommandVerifier,
    )

    keys = get_workflow_authorization_key_ring()
    ownership = SQLAlchemyExecutionOwnershipStore(engine)
    checkpoints = SQLAlchemyCheckpointStore(engine)
    from bpmn_container.control_evidence import SyntheticControlEvidence

    controls = SyntheticControlEvidence(engine=engine, checkpoints=checkpoints, keys=keys)
    app.extensions['bpmn_synthetic_control_evidence'] = controls
    orchestrator = NativeGraphOrchestrator(
        queue=build_native_graph_task_queue_adapter(), checkpoints=checkpoints,
        events=SQLAlchemyEventStore(engine), ownership=ownership, ledger=SQLAlchemySideEffectLedger(engine),
        key_ring=keys,
        command_verifier=WorkflowCommandVerifier(keys, SQLAlchemyWorkflowCommandReplayNonceStore(engine)),
        policy=HubGovernedNativeControlPolicy(),
        authorization_grants=SQLAlchemyWorkflowAuthorizationGrantService(engine),
        run_lease_service=controls,
    )
    from dataclasses import replace

    from bpmn_container.evidence import SyntheticTaskEvidence

    from agent.routes.tasks.autopilot_native_dispatch import NATIVE_DISPATCH_PORTS, production_native_dispatch_ports

    evidence = SyntheticTaskEvidence(engine=engine, keys=get_workflow_authorization_key_ring)
    app.extensions['bpmn_synthetic_task_evidence'] = evidence
    app.extensions[NATIVE_DISPATCH_PORTS.extension_key] = replace(production_native_dispatch_ports(),
                                                                prepare_task=evidence.prepare)
    if os.environ.get("BPMN_WORKER_REDELIVERY_ENABLED") == "1":
        from bpmn_container.worker_redelivery import exercise_worker_redelivery

        return {"worker_redelivery": exercise_worker_redelivery(app, base, token, orchestrator, fixtures.service())}
    if os.environ.get("BPMN_WORKER_LOSS_ENABLED") == "1":
        from bpmn_container.worker_loss import exercise_worker_loss

        return {"worker_loss": exercise_worker_loss(app, base, token, orchestrator, fixtures.service())}
    observed = {
        'native_node': _exercise_scenario(app, base, token, orchestrator, fixtures.service(), 'native', 1),
        'artifact_handoff': _exercise_scenario(app, base, token, orchestrator,
            fixtures.artifact_handoff(), 'artifacts', 2),
    }
    # Shell isolation stays a focused two-scenario gate; the full reference gate
    # exercises compiled control semantics without additional execution ports.
    if os.environ.get('BPMN_TOOL_ISOLATION_ENABLED') == '1':
        return observed
    for name, approved, default in (('xor_true', True, False), ('xor_false', False, False),
                                    ('xor_default', False, True)):
        observed[name] = _exercise_scenario(app, base, token, orchestrator, fixtures.xor(default=default), name, 1,
            input_data={'approved': approved}, expected_nodes={'yes_task' if approved else 'no_task'},
            expected_edge='yes' if approved else 'no')
    for name, count, values in (('loop', 3, [1, 2, 3]), ('subprocess', 1, [1])):
        observed[name] = _exercise_scenario(app, base, token, orchestrator, getattr(fixtures, name)(), name, count,
            input_data={'value': 0, 'private_note': 'must-not-delegate'}, expected_values=values)
    from bpmn_container.full_app_reference_control import approval_control, message_control

    for decision, count, status in (('approve', 1, 'completed'), ('reject', 0, 'failed'),
                                    ('blocked', 0, 'waiting_for_approval')):
        observed['policy_' + decision] = _exercise_scenario(app, base, token, orchestrator,
            fixtures.approval(), 'policy_' + decision, count, expected_status=status,
            control=approval_control(decision) if decision != 'blocked' else None)
    for kind in ('timer', 'message'):
        observed[kind] = _exercise_scenario(app, base, token, orchestrator, fixtures.catch(kind), kind, 1,
            expected_nodes={'work'}, expected_event='workflow.bpmn.catch.consumed',
            control=message_control if kind == 'message' else None)
    observed['and_join'] = _exercise_scenario(app, base, token, orchestrator, fixtures.parallel(), 'and_join', 3,
                                             expected_nodes={'fast', 'slow', 'after'}, parallel=True)
    return observed


def _exercise_scenario(app, base, token, orchestrator, xml, name, expected_tasks, *, input_data=None,
                       expected_nodes=None, expected_edge=None, expected_values=None,
                       expected_status='completed', expected_event=None, control=None, parallel=False):
    from bpmn_container.acceptance import compile_request
    from bpmn_container.full_app import observe_http

    from agent.repository import task_repo

    request = compile_request(xml, "full-app-autopilot-" + name, input_data)
    from bpmn_container.control_tasks import create_control_task

    with app.app_context():
        create_control_task(request)
        result = orchestrator.start(request)
    ticks = []
    driver = None
    if parallel:
        from bpmn_container.full_app_parallel import ParallelTick

        driver = ParallelTick(base=base, token=token)
    deadline = time.monotonic() + 25
    stopped = {'completed', 'failed', 'cancelled'} | ({'waiting_for_approval'} if control is None else set())
    response = None
    try:
        while result.status not in stopped and time.monotonic() < deadline:
            if control is not None:
                with app.app_context():
                    result = control(orchestrator, request, result)
                if result.status in stopped:
                    break
            response = (driver.tick() if driver is not None else observe_http(base, '/tasks/autopilot/tick', payload={},
                         headers={'Authorization': 'Bearer ' + token}, timeout=20))
            if response is not None:
                status, body = response
                assert status == 200, 'full_app_autopilot_tick_failed'
                ticks.append({name: body.get('data', {}).get(name) for name in ('dispatched', 'completed', 'failed')})
            with app.app_context():
                result = orchestrator.advance(request)
                if driver is not None:
                    tasks = [task for task in task_repo.get_all() if (task.worker_execution_context or {}).get(
                        'native_node_command', {}).get('run_id') == request.run_id]
                    driver.observe_join(result=result, tasks=tasks)
            time.sleep(0.02)
    finally:
        if driver is not None:
            response = driver.close()
    if driver is not None:
        if response is not None:
            status, body = response
            assert status == 200, 'full_app_autopilot_parallel_tick_failed'
            ticks.append({name: body.get('data', {}).get(name) for name in ('dispatched', 'completed', 'failed')})
        assert driver.pending_sibling_observed, 'full_app_and_pending_sibling_not_observed'
    assert result.status == expected_status, 'full_app_autopilot_reference_status_mismatch:' + name
    with app.app_context():
        tasks = [task for task in task_repo.get_all() if
                 (task.worker_execution_context or {}).get("native_node_command", {}).get("run_id") == request.run_id]
    assert len(tasks) == expected_tasks, "full_app_autopilot_task_count"
    from sqlmodel import Session, select

    from agent.database import engine
    from agent.db_models.native_dispatch import NativeDispatchDB

    with Session(engine) as session:
        journal = list(session.exec(select(NativeDispatchDB).where(
            NativeDispatchDB.task_id.in_([task.id for task in tasks]),
        )))
    assert len(journal) == expected_tasks and all(row.state == "accepted" for row in journal), (
        "full_app_autopilot_durable_dispatch_not_settled"
    )
    for task in tasks:
        receipt = task.verification_status.get("native_result_receipt")
        assert task.status == "completed" and receipt, "full_app_autopilot_hub_receipt_missing"
        assert receipt["classification"] == "technical_observation", "full_app_autopilot_receipt_classification"
        output = task.verification_status["native_node_result"]["output_data"]
        if os.environ.get("BPMN_TOOL_ISOLATION_ENABLED") == "1":
            assert output.get("tool_isolation", {}).get("passed") is True, "full_app_tool_isolation_missing"
        assert output["worker_hostname"] != socket.gethostname(), "full_app_autopilot_container_not_separate"
        assert task.assigned_agent_url == WORKER_URL, "full_app_autopilot_recipient_changed"
        assert not task.last_proposal, "full_app_autopilot_llm_proposal_used"
    assert sum(tick["completed"] or 0 for tick in ticks) == expected_tasks, "full_app_autopilot_not_dispatched"
    registry_observations = [app.extensions['bpmn_synthetic_task_evidence'].complete(task) for task in tasks]
    if name == "artifacts":
        outputs = [task.verification_status["native_node_result"]["output_data"] for task in tasks]
        assert any(item.get("consumer_bytes_verified") is True for item in outputs), (
            "full_app_artifact_bytes_unverified"
        )
    nodes = [task.worker_execution_context['native_node_command']['node']['node_id'] for task in tasks]
    if expected_nodes is not None:
        assert set(nodes) == expected_nodes, 'full_app_reference_selected_tasks_mismatch'
    if expected_values is not None:
        values = sorted(task.verification_status['native_node_result']['output_data']['value'] for task in tasks)
        assert values == expected_values, 'full_app_reference_region_values_mismatch'
        for task in tasks:
            inputs = task.worker_execution_context['native_node_command']['input_data']
            assert set(inputs['workflow_input']) == {'value'}, 'full_app_reference_private_input_leaked'
            assert inputs['dependency_results'] == {}, 'full_app_reference_dependency_projection_broadened'
    with app.app_context():
        events = list(orchestrator.stream(request))
        assert orchestrator.advance(request).status == expected_status, 'full_app_reference_terminal_replay_changed'
    delegated = [event.step_id for event in events if event.event_type == 'workflow.step.delegated']
    from ananta_contracts.bpmn_edge_event import valid_bpmn_edge_observation

    directions = {edge.edge_id: (edge.source, edge.target) for edge in request.plan.edges}
    edge_events = [event for event in events if event.event_type.startswith('workflow.bpmn.edge.')]
    assert all(valid_bpmn_edge_observation(event.to_dict(), tenant_id=request.plan.tenant_id,
        workflow_id=request.plan.workflow_id, run_id=request.run_id, plan_hash=request.plan.plan_hash,
        definition_hash=request.plan.metadata['bpmn_definition_hash'], directions=directions)
        for event in edge_events), 'full_app_edge_event_binding_invalid'
    if expected_status == 'completed':
        assert {event.payload['edge_id'] for event in edge_events} == set(directions), (
            'full_app_terminal_edge_observation_missing')
    assert sorted(delegated) == sorted(nodes), 'full_app_reference_actual_delegation_events_mismatch'
    if expected_edge is not None:
        assert any(event.step_id == 'choose' and event.payload.get('selected_edge') == expected_edge
                   for event in events), 'full_app_reference_xor_edge_not_observed'
    if expected_event is not None:
        assert sum(event.event_type == expected_event for event in events) == 1, 'full_app_reference_event_count'
    if parallel:
        outputs = {task.worker_execution_context['native_node_command']['node']['node_id']:
                   task.verification_status['native_node_result']['output_data'] for task in tasks}
        assert outputs['after']['started_at'] >= max(outputs[node]['finished_at'] for node in ('fast', 'slow'))
        completed = {event.step_id: event.sequence for event in events if event.event_type == 'workflow.step.completed'}
        after = next(event.sequence for event in events if event.event_type == 'workflow.step.delegated'
                     and event.step_id == 'after')
        assert after > max(completed['fast'], completed['slow']), 'full_app_and_event_order_mismatch'
    if name in {'policy_approve', 'policy_reject'}:
        expected = 'workflow.approval.granted' if name == 'policy_approve' else 'workflow.approval.rejected'
        assert any(event.event_type == expected for event in events), 'full_app_reference_policy_event_missing'
    return_event_types = [event.event_type for event in events]
    return {"passed": True, "workflow_status": result.status, "task_status": tasks[-1].status if tasks else None,
            "real_autopilot_http_tick": True, "separate_worker": True, "signed_closed_projection": True,
            "hub_receipt_classification": receipt["classification"] if tasks else None,
            "generic_llm_proposal_used": False,
            "ticks": ticks, "production_release_evidence": False,
            "task_count": expected_tasks, "durable_dispatch_journal": "accepted",
            "bootstrap_fixture": "Synthetic Hub graph input; production queue/dispatch/auth/result services",
            'executed_nodes': nodes, 'canonical_event_types': return_event_types,
            'selected_edge': expected_edge, 'projected_values': expected_values,
            'hub_registry_test_runs': registry_observations,
            'canonical_edge_observation_count': len(edge_events),
            'hub_registry_control_runs': app.extensions['bpmn_synthetic_control_evidence'].observations[request.run_id]}

