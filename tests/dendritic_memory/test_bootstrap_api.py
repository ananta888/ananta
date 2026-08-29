from __future__ import annotations

from flask import Flask

from agent.bootstrap.dendritic_memory import initialize_dendritic_memory
from tests.dendritic_memory.helpers import spec


def _wire(app, tmp_path, *, enabled=True, mode="mock") -> None:
    app.config.update(
        ROLE="hub",
        ANANTA_DENDRITIC_MEMORY_ENABLED=enabled,
        ANANTA_DENDRITIC_MEMORY_MODE=mode,
        ANANTA_DENDRITIC_MEMORY_RUNTIME_ENABLED=False,
        ANANTA_DENDRITIC_MEMORY_AUTOMATIC_ACTIVATION_ENABLED=False,
        ANANTA_DENDRITIC_MEMORY_STATE=str(tmp_path / "dendritic.sqlite3"),
        ANANTA_DENDRITIC_MEMORY_ARTIFACT_ROOT=str(tmp_path / "packs"),
    )
    initialize_dendritic_memory(app)


def test_composition_is_hub_only_default_off_and_never_requires_human(tmp_path) -> None:
    hub = Flask("hub")
    hub.secret_key = "test-secret"
    _wire(hub, tmp_path, enabled=False, mode="disabled")
    capability = hub.extensions["dendritic_memory_capabilities"].projection()
    assert capability["state"] == "disabled"
    assert capability["human_intervention_required"] is False

    worker = Flask("worker")
    worker.secret_key = "test-secret"
    worker.config["ROLE"] = "worker"
    status = initialize_dendritic_memory(worker)
    assert status.ready is False
    assert status.reason_code == "dendritic_hub_role_required"


def test_api_dry_run_create_list_cancel_is_fully_automatic(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    payload = spec().to_dict()
    payload.pop("tenant_id")
    dry = client.post(
        "/api/ml-intern-training/dendritic-memory/dry-run", headers=admin_auth_header, json={"spec": payload}
    )
    assert dry.status_code == 200
    assert dry.get_json()["data"]["admissible"] is True
    created = client.post(
        "/api/ml-intern-training/dendritic-memory/runs",
        headers={**admin_auth_header, "Idempotency-Key": "request-0001"},
        json={"spec": payload},
    )
    assert created.status_code == 200
    run = created.get_json()["data"]
    assert "worker_authorization" not in run
    listed = client.get("/api/ml-intern-training/dendritic-memory/runs", headers=admin_auth_header)
    assert listed.status_code == 200
    assert len(listed.get_json()["data"]["items"]) == 1
    cancelled = client.post(
        f"/api/ml-intern-training/dendritic-memory/runs/{run['run_id']}/cancel",
        headers=admin_auth_header,
        json={"expected_revision": 1},
    )
    assert cancelled.status_code == 200
    assert cancelled.get_json()["data"]["state"] == "cancelled"


def test_api_rejects_cross_tenant_and_malformed_payload(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    cross_tenant = client.post(
        "/api/ml-intern-training/dendritic-memory/dry-run",
        headers=admin_auth_header,
        json={"spec": spec(tenant_id="foreign-tenant").to_dict()},
    )
    assert cross_tenant.status_code == 403
    malformed = client.post(
        "/api/ml-intern-training/dendritic-memory/runs",
        headers={**admin_auth_header, "Content-Type": "application/json"},
        data="not-json",
    )
    assert malformed.status_code == 422
