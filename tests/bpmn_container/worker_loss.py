"""Actual Worker process loss before result response; Hub remains alive and bounded."""

import time


def exercise_worker_loss(app, base, token, orchestrator, xml):
    from bpmn_container.acceptance import compile_request
    from bpmn_container.full_app import observe_http
    from sqlmodel import Session, select

    from agent.database import engine
    from agent.db_models.native_dispatch import NativeDispatchDB
    from agent.repository import task_repo
    from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
    from agent.services.native_dispatch_reconciliation import reconcile_native_dispatches

    request = compile_request(xml, "full-app-worker-loss")
    from bpmn_container.control_tasks import create_control_task

    with app.app_context():
        create_control_task(request)
        orchestrator.start(request)
    headers = {"Authorization": "Bearer " + token}
    deadline = time.monotonic() + 35
    journal = []
    ticks = []
    while time.monotonic() < deadline:
        status, body = observe_http(base, "/tasks/autopilot/tick", payload={}, headers=headers)
        assert status == 200, "worker_loss_autopilot_tick_failed"
        ticks.append(body.get("data", {}))
        with Session(engine) as session:
            journal = list(session.exec(select(NativeDispatchDB)))
        if journal:
            break
        with app.app_context():
            orchestrator.advance(request)
        time.sleep(0.02)
    assert len(journal) == 1 and journal[0].state == "unknown", "worker_loss_outcome_not_durable"
    row = journal[0]
    with app.app_context():
        task = task_repo.get_by_id(row.task_id)
        assert task.status == "waiting_for_review", "worker_loss_task_not_blocked"
        assert not task.verification_status.get("native_result_receipt"), "worker_loss_invented_receipt"
        assert not task.last_proposal, "worker_loss_llm_fallback"
        # Explicit fixture-driven Hub status reset through the normal repository.
        # Neither this reset nor the following tick authorizes a new send claim.
        task.status = "todo"
        task_repo.save(task)
    for _ in range(2):
        status, body = observe_http(base, "/tasks/autopilot/tick", payload={}, headers=headers)
        ticks.append(body.get('data', {}))
        assert status == 200, "worker_loss_followup_tick_failed"
        assert not body.get("data", {}).get("completed"), "worker_loss_fabricated_completion"
    with Session(engine) as session:
        current = session.get(NativeDispatchDB, row.id)
        assert (current.claim == row.claim and current.state == "unknown"
                and current.binding_digest == row.binding_digest), "worker_loss_second_send_claim"
        assert len(list(session.exec(select(NativeDispatchDB)))) == 1, "worker_loss_duplicate_dispatch"
    with app.app_context():
        task = task_repo.get_by_id(row.task_id)
        import json

        from agent.services.organization_task_dispatch_gate_service import get_organization_task_dispatch_gate_service
        from agent.services.recovery_dispatch_gate_service import get_recovery_dispatch_gate_service

        print('BPMN_WORKER_LOSS_RESET_STATE=' + json.dumps({
            'status': task.status, 'parent': task.parent_task_id, 'dependencies': task.depends_on,
            'manual_override_active': (task.manual_override_until or 0) > time.time(),
            'recovery_gate': get_recovery_dispatch_gate_service().evaluate_task(task).reason_code,
            'organization_gate': get_organization_task_dispatch_gate_service().evaluate(task).reason_code,
            'ticks': [{key: item.get(key) for key in ('dispatched', 'failed', 'completed', 'reason')}
                      for item in ticks],
        }), flush=True)
        assert task.status == "waiting_for_review", "worker_loss_status_reset_not_contained"
        assert not task.verification_status.get("native_result_receipt"), "worker_loss_late_invented_receipt"
        reconciliation = reconcile_native_dispatches(
            journal=SQLAlchemyNativeDispatchJournal(engine), task_repository=task_repo,
            tenant_id=task.tenant_id, limit=1,
        )
        workflow = orchestrator.advance(request)
    assert reconciliation["unresolved"] == 1 and reconciliation["accepted"] == 0
    assert workflow.status != "completed", "worker_loss_workflow_fabricated_completion"
    evidence = app.extensions['bpmn_synthetic_task_evidence'].pending(task)
    return {"passed": True, "loss_boundary": "Worker process exit after execution before HTTP result response",
            "durable_dispatch_journal": "unknown", "unchanged_dispatch_claim": True,
            "followup_autopilot_ticks": 2, "hub_receipt_created": False, "generic_llm_proposal_used": False,
            "headless_reconciliation": "unresolved", "workflow_completed": False,
            "task_status": task.status, "status_reset_contained": True,
            "worker_exit_requires_host_docker_inspection": True, "production_release_evidence": False,
            "bootstrap_fixture": "Synthetic graph admission, deterministic handler and explicit Hub status reset",
            'hub_registry_worker_run': evidence,
            'hub_registry_control_runs': list(
                app.extensions['bpmn_synthetic_control_evidence'].observations[request.run_id])}
