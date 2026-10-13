"""Real Worker recreation and persisted-result admission through the full Hub."""

import os
import socket
import time
import urllib.error


def exercise_worker_redelivery(app, base, token, orchestrator, xml):
    from bpmn_container.transport import WORKER_URL, request_json
    from bpmn_container.worker_loss import exercise_worker_loss
    from sqlmodel import Session, select

    from agent.database import engine
    from agent.db_models.native_dispatch import NativeDispatchDB
    from agent.repository import task_repo


    containment = exercise_worker_loss(app, base, token, orchestrator, xml)
    with Session(engine) as session:
        journal = session.exec(select(NativeDispatchDB)).one()
    print("BPMN_WORKER_RESTART_REQUEST=" + os.environ["BPMN_RESTART_TOKEN"], flush=True)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            health = request_json(WORKER_URL + "/health", token=os.environ["BPMN_WORKER_TOKEN"])
            assert health["hostname"] != socket.gethostname()
            break
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    else:
        raise AssertionError("redelivery_worker_not_recreated")
    with app.app_context():
        task = task_repo.get_by_id(journal.task_id)
        if os.environ.get("BPMN_NATIVE_RETRY_ENABLED") == "1":
            from bpmn_container.expired_retry import exercise_expired_retry

            return exercise_expired_retry(app, base, token, orchestrator, xml, journal)
        app.config["ANANTA_NATIVE_RESULT_RECOVERY_ENABLED"] = True
    from bpmn_container.full_app import observe_http

    status, tick = observe_http(base, "/tasks/autopilot/tick", payload={},
                                headers={"Authorization": "Bearer " + token})
    assert status == 200 and tick["data"]["recovered"] == 1, "redelivery_scheduler_admission_failed"
    assert tick["data"]["dispatched"] == 0, "redelivery_scheduler_reexecuted"
    with app.app_context():
        accepted = task_repo.get_by_id(task.id)
        assert accepted.status == "completed", "redelivery_task_not_completed"
        receipt = accepted.verification_status["native_result_receipt"]
        assert receipt["classification"] == "technical_observation"
        output = accepted.verification_status["native_node_result"]["output_data"]
        assert output["worker_hostname"] != health["hostname"], "redelivery_result_was_reexecuted"
        from bpmn_container.acceptance import compile_request

        request = compile_request(xml, "full-app-worker-loss")
        workflow = orchestrator.advance(request)
    assert workflow.status == "completed", "redelivery_workflow_not_completed"
    with Session(engine) as session:
        current = session.get(NativeDispatchDB, journal.id)
        assert current.state == "accepted" and current.claim == journal.claim
    evidence = app.extensions['bpmn_synthetic_task_evidence'].complete(accepted)
    assert evidence['run_id'] == containment['hub_registry_worker_run']['run_id'], 'redelivery_registry_run_reissued'
    return {"passed": True, "containment": containment, "persisted_result_from_original_worker": True,
            "current_worker_hostname_differs_from_result": True, "workflow_status": "completed",
            "task_status": "completed", "journal_state": "accepted", "original_dispatch_claim_preserved": True,
            "scheduler_http_tick": True, "production_release_evidence": False,
            "hub_receipt_classification": "technical_observation", 'hub_registry_worker_run': evidence,
            'hub_registry_control_runs': app.extensions['bpmn_synthetic_control_evidence'].observations[request.run_id]}
