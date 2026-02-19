def test_autonomous_flow_ingest_claim_complete_read_model(client):
    ingest = client.post(
        "/tasks/orchestration/ingest",
        json={
            "title": "Implement API split",
            "description": "Split hub api service into domain clients",
            "source": "ui",
            "created_by": "operator",
            "priority": "high",
        },
    )
    assert ingest.status_code == 200
    task_id = ingest.json["data"]["id"]

    claim = client.post(
        "/tasks/orchestration/claim",
        json={
            "task_id": task_id,
            "agent_url": "http://alpha:5001",
            "idempotency_key": "autonomous-flow-1",
            "lease_seconds": 120,
        },
    )
    assert claim.status_code == 200
    assert claim.json["data"]["claimed"] is True

    complete = client.post(
        "/tasks/orchestration/complete",
        json={
            "task_id": task_id,
            "actor": "http://alpha:5001",
            "gate_results": {"passed": True, "checks": ["lint", "test"]},
            "output": "all checks passed",
            "trace_id": "trace-autonomous-001",
        },
    )
    assert complete.status_code == 200
    assert complete.json["data"]["status"] == "completed"

    model = client.get("/tasks/orchestration/read-model")
    assert model.status_code == 200
    data = model.json["data"]
    assert data["queue"]["completed"] >= 1
    assert any(t["id"] == task_id and t["status"] == "completed" for t in data["recent_tasks"])

