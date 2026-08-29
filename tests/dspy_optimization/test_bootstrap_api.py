from __future__ import annotations

from flask import Flask

from agent.bootstrap.dspy_optimization import initialize_dspy_optimization
from tests.dspy_optimization.helpers import spec


def _wire(app, tmp_path, *, enabled: bool = True, mode: str = "mock") -> None:
    app.config.update(
        ROLE="hub",
        ANANTA_DSPY_OPTIMIZATION_ENABLED=enabled,
        ANANTA_DSPY_OPTIMIZATION_MODE=mode,
        ANANTA_DSPY_OPTIMIZATION_STATE=str(tmp_path / "dspy.sqlite3"),
        ANANTA_DSPY_OPTIMIZATION_ARTIFACT_ROOT=str(tmp_path / "artifacts"),
    )
    initialize_dspy_optimization(app)


def test_dspy_composition_is_hub_only_and_default_off(tmp_path) -> None:
    hub = Flask("hub")
    hub.secret_key = "test-secret"
    _wire(hub, tmp_path, enabled=False, mode="disabled")
    assert hub.extensions["dspy_optimization_wiring_status"].ready is True
    assert hub.extensions["dspy_engine_capabilities"].projection()["state"] == "disabled"

    worker = Flask("worker")
    worker.secret_key = "test-secret"
    worker.config["ROLE"] = "worker"
    status = initialize_dspy_optimization(worker)
    assert status.ready is False
    assert status.reason_code == "dspy_hub_role_required"
    assert "dspy_optimization_jobs" not in worker.extensions

    missing_secret = Flask("missing-secret")
    _wire(missing_secret, tmp_path)
    assert missing_secret.extensions["dspy_optimization_wiring_status"].ready is False
    assert missing_secret.extensions["dspy_optimization_wiring_status"].reason_code == "dspy_configuration_invalid"


def test_api_dry_run_create_list_and_cancel_are_fully_headless(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    dry = client.post("/api/dspy-optimization/dry-run", headers=admin_auth_header, json={"spec": spec().to_dict()})
    assert dry.status_code == 200
    assert dry.get_json()["data"]["model_call_performed"] is False
    created = client.post(
        "/api/dspy-optimization/runs",
        headers={**admin_auth_header, "Idempotency-Key": "request-1"},
        json={"spec": spec().to_dict()},
    )
    assert created.status_code == 200
    run = created.get_json()["data"]
    replay = client.post(
        "/api/dspy-optimization/runs",
        headers={**admin_auth_header, "Idempotency-Key": "request-1"},
        json={"spec": spec().to_dict()},
    ).get_json()["data"]
    assert replay["run_id"] == run["run_id"]
    assert replay["replayed"] is True
    listed = client.get("/api/dspy-optimization/runs?tenant_id=tenant-1", headers=admin_auth_header)
    assert listed.status_code == 200
    assert len(listed.get_json()["data"]["items"]) == 1
    cancelled = client.post(
        f"/api/dspy-optimization/runs/{run['run_id']}/cancel",
        headers=admin_auth_header,
        json={"tenant_id": "tenant-1", "expected_revision": 1},
    )
    assert cancelled.status_code == 200
    assert cancelled.get_json()["data"]["state"] == "cancelled"
    assert cancelled.get_json()["data"]["human_intervention_required"] is False


def test_api_mutations_require_admin(app, client, user_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    response = client.post(
        "/api/dspy-optimization/runs",
        headers={**user_auth_header, "Idempotency-Key": "request-1"},
        json={"spec": spec().to_dict()},
    )
    assert response.status_code == 403


def test_api_rejects_malformed_payloads_without_operator_intervention(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    response = client.post(
        "/api/dspy-optimization/runs",
        headers={**admin_auth_header, "Content-Type": "application/json"},
        data="not-json",
    )
    assert response.status_code == 422
    assert response.get_json()["message"] == "dspy_payload_invalid"
