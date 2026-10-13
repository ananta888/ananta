"""Full production application bootstrap in its own isolated container."""

import base64
import json
import os
import secrets
import threading
import traceback
import urllib.error
import urllib.request
from pathlib import Path

from bpmn_container.transport import require_isolation


def observe_http(base, path, *, payload=None, headers=None, timeout=5):
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ValueError('full_app_http_timeout_invalid')
    request = urllib.request.Request(base + path, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(1048577)
        assert len(body) <= 1048576, "full_app_response_limit"
        return response.status, json.loads(body)


def run():
    require_isolation("hub")
    assert os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") == "1"
    signing = {"schema": "ananta.workflow-auth-signing-keyring.v1", "algorithm": "ed25519",
               "active_key_id": "full-app-synthetic",
               "private_keys": {"full-app-synthetic": base64.b64encode(secrets.token_bytes(32)).decode()}}
    path = Path("/sandbox/secrets/full-app-signing.json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(signing, stream)
    path.chmod(0o600)
    os.environ["ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE"] = str(path)
    os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
    os.environ["INITIAL_ADMIN_PASSWORD"] = secrets.token_urlsafe(48)
    autopilot_enabled = os.environ.get("BPMN_AUTOPILOT_ENABLED") == "1"
    if autopilot_enabled:
        os.environ["AGENT_TOKEN"] = secrets.token_urlsafe(48)

    from werkzeug.serving import make_server

    from agent.ai_agent import create_app

    app = create_app(testing=False)
    assert app.config["TESTING"] is False and app.config["ROLE"] == "hub", "full_app_production_role_failed"
    from agent.services.memory_runtime import HubMemoryRuntime, get_memory_runtime

    with app.app_context():
        assert isinstance(get_memory_runtime(), HubMemoryRuntime), "full_app_hub_memory_composition_failed"
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    required = {"/health", "/login", "/api/visual-process/workflow/start",
                "/api/internal/tasks/<task_id>/native-artifacts",
                "/api/internal/tasks/<task_id>/native-artifact-inputs/<input_name>"}
    assert required <= rules, "full_app_native_routes_missing"
    if autopilot_enabled:
        from bpmn_container.full_app_autopilot import prepare_worker

        prepare_worker(app)
    server = make_server("0.0.0.0" if autopilot_enabled else "127.0.0.1",
                         8080 if autopilot_enabled else 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    observations = {}
    try:
        status, _body = observe_http(base, "/health")
        assert status == 200, "full_app_health_failed"
        observations["health"] = status
        status, body = observe_http(base, "/login", payload={
            "username": "admin", "password": os.environ["INITIAL_ADMIN_PASSWORD"],
        })
        assert status == 200 and isinstance(body.get("data", {}).get("access_token"), str), "full_app_login_failed"
        observations["login"] = {"status": status, "access_token_issued": True}
        session_token = body["data"]["access_token"]
        for name, endpoint, payload in (
            ("native_publish", "/api/internal/tasks/not-admitted/native-artifacts", None),
            ("native_input", "/api/internal/tasks/not-admitted/native-artifact-inputs/report", None),
            ("workflow_start", "/api/visual-process/workflow/start", {"workflow_id": "untrusted"}),
        ):
            status, body = observe_http(base, endpoint, payload=payload)
            assert status == 401, "full_app_unauthenticated_boundary_failed:" + name
            observations[name] = {"status": status, "data": body.get("data")}
        from sqlmodel import Session, select

        from agent.database import engine
        from agent.db_models import TaskDB

        with Session(engine) as session:
            assert session.exec(select(TaskDB.id)).all() == [], "full_app_unauthorized_task_created"
        if autopilot_enabled:
            from bpmn_container.full_app_autopilot import exercise_dispatch

            observations["native_autopilot"] = exercise_dispatch(app, base, session_token)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive(), "full_app_server_shutdown_timeout"
    return {"passed": True, "testing_flag": False, "production_bootstrap": True,
            "required_routes_registered": sorted(required), "http_boundaries": observations,
            "unauthorized_tasks_created": 0, "background_services_configured_off": True}


def main():
    report = {"schema": "bpmn_full_app_bootstrap.v1", "capability_scope": "full_application_bootstrap_only",
              "evidence_classification": "synthetic_test_technical_observation", "production_release_evidence": False,
              "not_covered": ["positive_autopilot_dispatch", "real_container_loss_recovery",
                              "production_release_admission"],
              "cases": {}}
    if os.environ.get("BPMN_AUTOPILOT_ENABLED") == "1":
        report.update(schema="bpmn_full_app_native_autopilot.v1", capability_scope="full_application_native_autopilot")
        report["not_covered"].remove("positive_autopilot_dispatch")
        report["not_covered"].append("public_production_release_admission")
        if os.environ.get("BPMN_WORKER_LOSS_ENABLED") == "1":
            report.update(schema="bpmn_full_app_worker_loss.v1", capability_scope="worker_loss_containment")
            report["not_covered"].append("worker_result_redelivery_and_workflow_completion")
        if os.environ.get("BPMN_WORKER_REDELIVERY_ENABLED") == "1":
            report.update(schema="bpmn_full_app_worker_redelivery.v1", capability_scope="worker_recreation_redelivery")
            report["not_covered"] = ["lease_expiry_recovery", "postgresql_concurrency", "production_release_admission"]
        if os.environ.get("BPMN_NATIVE_RETRY_ENABLED") == "1":
            report.update(schema="bpmn_full_app_expired_retry.v1", capability_scope="expired_dispatch_explicit_retry")
            report["not_covered"] = ["production_provider_effects", "postgresql_concurrency",
                                     "production_release_admission"]
        if os.environ.get("BPMN_TOOL_ISOLATION_ENABLED") == "1":
            report.update(schema="bpmn_full_app_tool_isolation.v1", capability_scope="private_store_shell_isolation")
            report["not_covered"] = ["other_tool_or_provider_execution_ports", "aarch64_kernel_execution",
                                     "production_release_admission"]
    try:
        report["cases"]["full_application_boot"] = run()
        report["passed"] = True
    except Exception as exc:
        report["cases"]["full_application_boot"] = {"passed": False,
                                                     "error_type": type(exc).__name__,
                                                     "failure_frames": [
                                                         {"module": Path(frame.filename).name,
                                                          "function": frame.name, "line": frame.lineno}
                                                         for frame in traceback.extract_tb(exc.__traceback__)[-12:]
                                                     ],
                                                     "missing_module": getattr(exc, "name", None),
                                                     "missing_file": getattr(exc, "filename", None),
                                                     "reason_code": (str(exc) if isinstance(exc, AssertionError)
                                                                     and str(exc).startswith("full_app_")
                                                                     else "full_application_bootstrap_failed")}
        report["passed"] = False
        # Full startup phase diagnostics precede this bounded summary.
    print("BPMN_CONTAINER_REPORT=" + json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
