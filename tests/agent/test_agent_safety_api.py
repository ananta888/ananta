from __future__ import annotations

from agent.bootstrap.agent_safety import initialize_agent_safety
from agent.services.agent_safety_ports import RecordingSafetyAdapter


def _wire(app, tmp_path):
    adapter = RecordingSafetyAdapter()
    app.config.update(ROLE="hub", ANANTA_AGENT_SAFETY_STATE=str(tmp_path / "agent-safety-api.sqlite3"))
    initialize_agent_safety(
        app,
        sandbox_control=adapter,
        egress_fence=adapter,
        credential_revocation=adapter,
    )
    return adapter


def _policy_payload():
    return {
        "policy_id": "policy-1",
        "revision": 1,
        "mode": "adversarial_eval",
        "preventive_policy_enabled": False,
        "preventive_training_enabled": False,
        "telemetry_enabled": True,
        "external_kill_switch_enabled": True,
        "incident_freeze_enabled": True,
        "adversarial_scope": ["local:fixture"],
        "global_stop_scope": "run",
        "max_parallel_agents": 2,
    }


def test_agent_safety_api_runs_a_fully_automatic_isolated_flow(app, client, admin_auth_header, tmp_path):
    _wire(app, tmp_path)
    assert (
        client.post("/api/agent-safety/policies", headers=admin_auth_header, json=_policy_payload()).status_code == 200
    )
    created = client.post(
        "/api/agent-safety/runs",
        headers=admin_auth_header,
        json={
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "run_id": "run-1",
            "group_id": "group-1",
            "policy_id": "policy-1",
            "target_ref": "local:fixture",
            "agents": [{"agent_id": "agent-1", "sandbox_id": "sandbox-1"}],
        },
    )
    assert created.status_code == 200
    boundary = client.post(
        "/api/agent-safety/boundaries",
        headers=admin_auth_header,
        json={
            "run_id": "run-1",
            "sandbox_id": "sandbox-1",
            "agent_id": "agent-1",
            "boundary_class": "filesystem",
            "outcome": "crossed",
            "detector_id": "fixture-detector",
            "metadata": {"secret": "never-return"},
        },
    )
    assert boundary.status_code == 200
    assert boundary.get_json()["data"]["containment"]["state"] == "enforced"
    overview = client.get(
        "/api/agent-safety/overview?project_id=project-1",
        headers=admin_auth_header,
    )
    assert overview.status_code == 200
    payload = overview.get_json()["data"]
    assert payload["runs"][0]["execution_allowed"] is False
    assert payload["containment_available"] is True
    assert payload["human_intervention_required"] is False


def test_agent_safety_api_rejects_external_scope_without_waiting_for_a_human(app, client, admin_auth_header, tmp_path):
    _wire(app, tmp_path)
    client.post("/api/agent-safety/policies", headers=admin_auth_header, json=_policy_payload())
    response = client.post(
        "/api/agent-safety/runs",
        headers=admin_auth_header,
        json={
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "run_id": "run-1",
            "group_id": "group-1",
            "policy_id": "policy-1",
            "target_ref": "https:external",
            "agents": [{"agent_id": "agent-1", "sandbox_id": "sandbox-1"}],
        },
    )
    assert response.status_code == 403
    assert response.get_json()["message"] == "agent_safety_target_not_authorized"


def test_agent_safety_mutations_require_admin_authority(app, client, user_auth_header, tmp_path):
    _wire(app, tmp_path)
    response = client.post("/api/agent-safety/policies", headers=user_auth_header, json=_policy_payload())
    assert response.status_code == 403
