import pytest


@pytest.mark.parametrize("runtime_profile", ["local-dev", "compose-safe", "distributed-strict"])
def test_control_plane_flow_is_consistent_across_runtime_profiles(client, admin_auth_header, runtime_profile):
    cfg_res = client.post(
        "/config",
        json={"runtime_profile": runtime_profile},
        headers=admin_auth_header,
    )
    assert cfg_res.status_code == 200

    task_id = f"MATRIX-{runtime_profile}"
    ingest = client.post(
        "/tasks/orchestration/ingest",
        json={
            "id": task_id,
            "title": f"Matrix task {runtime_profile}",
            "description": "Validate orchestration flow across runtime profiles",
            "source": "ui",
            "created_by": "matrix-test",
            "status": "todo",
        },
        headers=admin_auth_header,
    )
    assert ingest.status_code == 200

    claim = client.post(
        "/tasks/orchestration/claim",
        json={
            "task_id": task_id,
            "agent_url": "http://matrix-worker:5001",
            "lease_seconds": 60,
            "idempotency_key": f"matrix-{runtime_profile}",
        },
        headers=admin_auth_header,
    )
    assert claim.status_code == 200
    assert claim.json["data"]["claimed"] is True

    complete = client.post(
        "/tasks/orchestration/complete",
        json={
            "task_id": task_id,
            "actor": "http://matrix-worker:5001",
            "gate_results": {"passed": True},
            "output": f"ok-{runtime_profile}",
            "trace_id": f"trace-{runtime_profile}",
        },
        headers=admin_auth_header,
    )
    assert complete.status_code == 200
    assert complete.json["data"]["status"] == "completed"

    read_model = client.get("/tasks/orchestration/read-model", headers=admin_auth_header)
    assert read_model.status_code == 200
    payload = read_model.json["data"]
    assert "worker_execution_reconciliation" in payload
    assert payload["queue"]["completed"] >= 1
    assert any(item["id"] == task_id and item["status"] == "completed" for item in (payload["recent_tasks"] or []))
