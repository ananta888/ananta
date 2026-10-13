"""The operator API is read-only, scoped, authenticated and allowlist-redacted."""

import json

import pytest
from flask import Flask, g, request

from agent.routes.harness import harness_bp
from agent.services.harness_operator_service import HarnessOperatorService
from tests.harness import test_harness_search as search_helpers

SCOPE = search_helpers.SCOPE
runtime = search_helpers.runtime
advance = search_helpers.advance
start = search_helpers.start


@pytest.fixture
def client(runtime, monkeypatch):
    start(runtime)
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN="synthetic-operator-token")
    app.extensions["harness_operator"] = HarnessOperatorService(runtime[0])
    app.register_blueprint(harness_bp)
    monkeypatch.setattr("agent.auth.log_audit", lambda *args, **kwargs: None)

    @app.before_request
    def automated_role_fixture():
        role = request.headers.get("X-Test-Role")
        if role is not None:
            g.is_admin = role == "admin"

    return app.test_client()


def path(suffix="", scope="tenant_id=tenant&project_id=project"):
    return f"/api/harness/searches{suffix}?{scope}"


def test_admin_can_inspect_snapshot_and_list_without_advancing(client, runtime):
    before = runtime[0].get(**SCOPE)
    result = client.get(path("/search-test"), headers={"X-Test-Role": "admin"})
    assert result.status_code == 200
    data = result.get_json()["data"]
    assert data["phase"] == "ready" and data["variants"][0]["is_baseline"]
    listed = client.get(path(), headers={"X-Test-Role": "admin"})
    assert listed.status_code == 200 and len(listed.get_json()["data"]["items"]) == 1
    assert runtime[0].get(**SCOPE) == before and runtime[1].submit_calls == 0


def test_non_admin_and_unauthenticated_requests_are_denied(client):
    assert client.get(path(), headers={"X-Test-Role": "viewer"}).status_code == 403
    assert client.get(path()).status_code == 401


@pytest.mark.parametrize(
    "scope,code",
    [
        ("tenant_id=other&project_id=project", 404),
        ("tenant_id=tenant&project_id=other", 404),
        ("tenant_id=tenant", 422),
    ],
)
def test_exact_tenant_project_scope_is_required(client, scope, code):
    assert client.get(path("/search-test", scope), headers={"X-Test-Role": "admin"}).status_code == code


@pytest.mark.parametrize("limit", ["0", "101", "bad"])
def test_list_bounds_are_validated(client, limit):
    response = client.get(
        path(scope=f"tenant_id=tenant&project_id=project&limit={limit}"), headers={"X-Test-Role": "admin"}
    )
    assert response.status_code == 422


def test_lineage_and_pareto_status_after_a_registered_evaluation(client, runtime):
    for _ in range(3):
        advance(runtime)
    response = client.get(path("/search-test/lineage"), headers={"X-Test-Role": "admin"})
    data = response.get_json()["data"]
    assert response.status_code == 200 and len(data["nodes"]) == 2 and len(data["edges"]) == 1
    assert any(node["pareto_status"] == "front" for node in data["nodes"])
    assert data["nodes"][1]["evidence"]["synthetic"] is True


def test_unknown_fields_raw_observations_and_secret_refs_are_never_projected(client, runtime):
    snapshot = runtime[0].get(**SCOPE)
    snapshot["state"]["raw_observation"] = "sensitive-observation-marker"
    snapshot["state"]["population"][0]["genome"]["secret_refs"] = ["vault://secret-marker"]
    snapshot["state"]["population"][0]["metrics"]["sensitive-metric-marker"] = 123
    snapshot["state"]["events"] = [{"raw_text": "sensitive-event-marker"}]
    runtime[0].append(**SCOPE, state=snapshot["state"], expected_revision=0)
    response = client.get(path("/search-test"), headers={"X-Test-Role": "admin"})
    encoded = json.dumps(response.get_json())
    assert response.status_code == 200
    for marker in (
        "sensitive-observation-marker",
        "secret-marker",
        "sensitive-metric-marker",
        "sensitive-event-marker",
    ):
        assert marker not in encoded


def test_api_has_no_implicit_execution_or_mutation_endpoint(client):
    assert client.post(path(), headers={"X-Test-Role": "admin"}, json={}).status_code == 405
