from agent.config import settings


def test_delegate_requires_hub_role(client, monkeypatch):
    client.post("/tasks", json={"id": "parent-1", "description": "parent task"})
    monkeypatch.setattr(settings, "role", "worker")
    res = client.post(
        "/tasks/parent-1/delegate",
        json={"agent_url": "http://alpha:5001", "subtask_description": "run tests"},
    )
    assert res.status_code == 403
    assert res.json["message"] == "hub_role_required"

