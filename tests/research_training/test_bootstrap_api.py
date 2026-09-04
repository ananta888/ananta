from __future__ import annotations

from flask import Flask

from agent.bootstrap.research_training import initialize_research_training
from tests.research_training.helpers import services, spec


def _wire(app: Flask, tmp_path, *, enabled: bool = True, mode: str = "mock") -> None:
    app.config.update(
        ROLE="hub",
        ANANTA_RESEARCH_TRAINING_ENABLED=enabled,
        ANANTA_RESEARCH_TRAINING_MODE=mode,
        ANANTA_RESEARCH_TRAINING_AUTOMATIC_RELEASE_ENABLED=False,
        ANANTA_RESEARCH_TRAINING_STATE=str(tmp_path / "research.sqlite3"),
        ANANTA_RESEARCH_TRAINING_ARTIFACT_ROOT=str(tmp_path / "artifacts"),
        ANANTA_RESEARCH_TRAINING_DATASET_ROOT=str(tmp_path / "datasets"),
        ANANTA_RESEARCH_TRAINING_RESULT_ROOT=str(tmp_path / "results"),
    )
    initialize_research_training(app)


def test_composition_is_hub_only_default_off_and_never_requires_human(tmp_path) -> None:
    hub = Flask("hub")
    hub.secret_key = "test-secret"
    _wire(hub, tmp_path, enabled=False, mode="disabled")
    capability = hub.extensions["research_training_capabilities"].projection()
    assert capability["state"] == "disabled"
    assert capability["human_intervention_required"] is False
    rollout = hub.extensions["research_training_rollout"].evaluate({})
    assert rollout["reason_code"] == "research_rollout_disabled"
    assert rollout["human_intervention_required"] is False

    worker = Flask("worker")
    worker.secret_key = "test-secret"
    worker.config["ROLE"] = "worker"
    status = initialize_research_training(worker)
    assert status.ready is False
    assert status.reason_code == "research_hub_role_required"


def test_api_dry_run_create_list_cancel_is_fully_automatic(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    _, recipes = services(tmp_path / "helper.sqlite3")
    payload = spec(recipes)
    payload.pop("tenant_id")
    dry = client.post(
        "/api/ml-intern-training/research/dry-run",
        headers=admin_auth_header,
        json={"spec": payload},
    )
    assert dry.status_code == 200
    assert dry.get_json()["data"]["admissible"] is True
    created = client.post(
        "/api/ml-intern-training/research/runs",
        headers={**admin_auth_header, "Idempotency-Key": "research-request-0001"},
        json={"spec": payload},
    )
    assert created.status_code == 201
    run = created.get_json()["data"]
    assert "worker_authorization" not in run
    assert run["human_intervention_required"] is False
    listed = client.get("/api/ml-intern-training/research/runs", headers=admin_auth_header)
    assert listed.status_code == 200
    assert len(listed.get_json()["data"]["items"]) == 1
    cancelled = client.post(
        f"/api/ml-intern-training/research/runs/{run['run_id']}/cancel",
        headers=admin_auth_header,
        json={"expected_revision": 1},
    )
    assert cancelled.status_code == 200
    assert cancelled.get_json()["data"]["state"] == "cancelled"


def test_api_rejects_cross_tenant_and_malformed_payload(app, client, admin_auth_header, tmp_path) -> None:
    _wire(app, tmp_path)
    _, recipes = services(tmp_path / "helper.sqlite3")
    foreign = spec(recipes)
    foreign["tenant_id"] = "foreign-tenant"
    cross_tenant = client.post(
        "/api/ml-intern-training/research/dry-run",
        headers=admin_auth_header,
        json={"spec": foreign},
    )
    assert cross_tenant.status_code == 403
    malformed = client.post(
        "/api/ml-intern-training/research/runs",
        headers={**admin_auth_header, "Content-Type": "application/json"},
        data="not-json",
    )
    assert malformed.status_code == 422


def test_openapi_inventory_covers_every_research_route_without_worker_addresses(
    app, client, admin_auth_header, tmp_path
) -> None:
    _wire(app, tmp_path)
    response = client.get("/api/ml-intern-training/research/openapi", headers=admin_auth_header)
    assert response.status_code == 200
    document = response.get_json()["data"]
    assert document["openapi"] == "3.1.0"
    assert "/api/ml-intern-training/research/runs/{run_id}/dispatch" in document["paths"]
    assert "/api/ml-intern-training/research/results/ingress" in document["paths"]
    assert "worker_url" not in str(document).lower()
