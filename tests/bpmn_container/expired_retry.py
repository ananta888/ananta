"""Expired results stay denied; only exact Hub preauthorization permits a fresh attempt."""

import time


def exercise_expired_retry(app, base, token, orchestrator, xml, journal):
    from bpmn_container.acceptance import compile_request
    from bpmn_container.full_app import observe_http
    from sqlmodel import Session, select

    from agent.database import engine
    from agent.db_models.native_dispatch import NativeDispatchDB
    from agent.db_models.workflow_runtime import WorkflowExecutionOwnershipDB
    from agent.repository import task_repo
    from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring
    from agent.services.workflow_runtime import SignedWorkflowCommand

    request = compile_request(xml, "full-app-worker-loss")
    old = task_repo.get_by_id(journal.task_id)
    original = old.worker_execution_context["native_node_command"]
    with Session(engine) as session:
        owner = session.exec(select(WorkflowExecutionOwnershipDB)).one()
        owner.lease_expires_at = time.time() - 1  # Explicit fault fixture, not a production renewal.
        session.add(owner)
        session.commit()
    app.config["ANANTA_NATIVE_RESULT_RECOVERY_ENABLED"] = True
    headers = {"Authorization": "Bearer " + token}
    status, tick = observe_http(base, "/tasks/autopilot/tick", payload={}, headers=headers)
    assert status == 200 and not tick["data"].get("recovered"), "expired_result_was_admitted"
    old = task_repo.get_by_id(journal.task_id)
    assert not old.verification_status.get("native_result_receipt"), "expired_receipt_created"
    keys = get_workflow_authorization_key_ring()

    def control(kind, name):
        checkpoint = orchestrator.checkpoint(request)
        command = SignedWorkflowCommand.issue(
            key_ring=keys, command_type=kind, tenant_id=checkpoint.tenant_id,
            workflow_id=checkpoint.workflow_id, run_id=checkpoint.run_id, step_id="__workflow__",
            checkpoint_id=checkpoint.checkpoint_id, expected_revision=checkpoint.revision,
            plan_hash=checkpoint.plan_hash, policy_version=checkpoint.policy_version,
            actor_id="synthetic-retry-admin", actor_roles=("admin",), now=time.time(),
            command_id=name, nonce=name,
        )
        print("BPMN_EXPIRED_STAGE=" + kind + ":" + name, flush=True)
        try:
            return orchestrator.resume(request, command=command)
        except Exception as exc:
            import traceback

            print("BPMN_EXPIRED_FAILURE=" + str([(frame.name, frame.lineno) for frame in
                  traceback.extract_tb(exc.__traceback__)]), flush=True)
            raise

    cancelled = control("cancel", "synthetic-expired-cancel")
    assert cancelled.status == "cancelled"
    try:
        control("retry", "synthetic-unapproved-retry")
    except PermissionError:
        pass
    else:
        raise AssertionError("uncertain_retry_without_policy")
    with Session(engine) as session:
        assert len(list(session.exec(select(NativeDispatchDB)))) == 1
    app.config["ANANTA_NATIVE_UNCERTAIN_RETRY_POLICY"] = [{
        "purpose": "native.retry.unknown_dispatch", "tenant_id": request.plan.tenant_id,
        "workflow_id": request.plan.workflow_id, "run_id": request.run_id, "plan_hash": original["plan_hash"],
        "policy_version": request.plan.policy_version, "step_id": original["node"]["node_id"],
        "actor_id": "synthetic-retry-admin", "dispatch_binding_digest": journal.binding_digest,
        "maximum_attempts": 2,
    }]
    result = control("retry", "synthetic-authorized-retry")
    deadline = time.monotonic() + 25
    while result.status != "completed" and time.monotonic() < deadline:
        status, tick = observe_http(base, "/tasks/autopilot/tick", payload={}, headers=headers)
        assert status == 200
        result = orchestrator.advance(request)
        time.sleep(0.02)
    assert result.status == "completed", "authorized_fresh_attempt_not_completed"
    tasks = [task for task in task_repo.get_all() if
             (task.worker_execution_context or {}).get("native_node_command", {}).get("run_id") == request.run_id]
    assert len(tasks) == 2
    fresh = next(task for task in tasks if task.id != old.id)
    command = fresh.worker_execution_context["native_node_command"]
    assert fresh.status == "completed" and command["attempt_id"] != original["attempt_id"]
    assert command["fencing_token"] > original["fencing_token"]
    assert not task_repo.get_by_id(old.id).verification_status.get("native_result_receipt")
    with Session(engine) as session:
        rows = list(session.exec(select(NativeDispatchDB)))
    assert len(rows) == 2 and {row.state for row in rows} == {"unknown", "accepted"}
    evidence = app.extensions['bpmn_synthetic_task_evidence']
    original_evidence = evidence.pending(task_repo.get_by_id(old.id))
    fresh_evidence = evidence.complete(fresh)
    assert original_evidence['run_id'] != fresh_evidence['run_id'], 'expired_retry_registry_identity_reused'
    return {"passed": True, "expired_result_admitted": False, "retry_without_policy_denied": True,
            "exact_hub_policy_permitted_fresh_attempt": True, "new_task_and_attempt": True,
            "higher_fencing_token": True, "old_journal_unknown": True, "workflow_status": "completed",
            "production_release_evidence": False,
            "fixture": "Explicit SQL expiry and exact Hub policy; synthetic handler",
            'hub_registry_original_run': original_evidence, 'hub_registry_fresh_run': fresh_evidence,
            'hub_registry_control_runs': app.extensions['bpmn_synthetic_control_evidence'].observations[request.run_id]}
