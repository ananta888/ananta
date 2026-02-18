def test_orchestration_ingest_and_read_model(client):
    r = client.post(
        "/tasks/orchestration/ingest",
        json={"title": "T1", "description": "implement feature", "source": "ui", "created_by": "tester"},
    )
    assert r.status_code == 200
    task_id = r.json["data"]["id"]

    rm = client.get("/tasks/orchestration/read-model")
    assert rm.status_code == 200
    data = rm.json["data"]
    assert data["queue"]["todo"] >= 1
    assert data["by_source"]["ui"] >= 1
    assert any(t["id"] == task_id for t in data["recent_tasks"])


def test_orchestration_claim_and_complete(client):
    r = client.post("/tasks/orchestration/ingest", json={"description": "fix bug", "source": "agent", "created_by": "alpha"})
    tid = r.json["data"]["id"]

    claim = client.post(
        "/tasks/orchestration/claim",
        json={"task_id": tid, "agent_url": "http://alpha:5001", "lease_seconds": 60, "idempotency_key": "k1"},
    )
    assert claim.status_code == 200
    assert claim.json["data"]["claimed"] is True

    done = client.post(
        "/tasks/orchestration/complete",
        json={"task_id": tid, "actor": "http://alpha:5001", "gate_results": {"passed": True}, "trace_id": "tr-1"},
    )
    assert done.status_code == 200
    assert done.json["data"]["status"] == "completed"
