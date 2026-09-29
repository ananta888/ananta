from __future__ import annotations

from agent.db_models import AgentInfoDB
from agent.repository import agent_repo


def test_terminal_targets_worker_only_for_user(client, user_auth_header, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "terminal_worker_target_enabled", True)
    monkeypatch.setattr(settings, "terminal_hub_target_enabled", True)
    monkeypatch.setattr(settings, "terminal_hub_as_worker_target_enabled", True)
    monkeypatch.setattr(settings, "hub_can_be_worker", True)

    agent_repo.save(AgentInfoDB(url="http://worker-a:5001", name="worker-a", role="worker", status="online"))

    res = client.get("/terminal/targets", headers=user_auth_header)
    assert res.status_code == 200
    payload = res.json["data"]["targets"]
    assert any(t["target_type"] == "worker" for t in payload)
    assert all(t["target_type"] != "hub" for t in payload)
    assert all(t["target_type"] != "hub_as_worker" for t in payload)


def test_terminal_targets_hub_visible_only_with_hub_list_permission(client, admin_auth_header, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "terminal_worker_target_enabled", True)
    monkeypatch.setattr(settings, "terminal_hub_target_enabled", True)
    monkeypatch.setattr(settings, "terminal_hub_as_worker_target_enabled", True)
    monkeypatch.setattr(settings, "hub_can_be_worker", True)

    # Override role policy for admin to include hub list but no hub write.
    app_cfg = client.application.config.setdefault("AGENT_CONFIG", {})
    app_cfg["terminal_policy"] = {
        "role_permissions": {
            "admin": [
                "terminal.worker.list",
                "terminal.worker.create",
                "terminal.worker.attach",
                "terminal.worker.read",
                "terminal.worker.write",
                "terminal.worker.kill",
                "terminal.hub.list",
            ]
        }
    }

    res = client.get("/terminal/targets", headers=admin_auth_header)
    assert res.status_code == 200
    payload = res.json["data"]["targets"]
    hub = [item for item in payload if item["target_type"] == "hub"]
    hub_as_worker = [item for item in payload if item["target_type"] == "hub_as_worker"]

    assert len(hub) == 1
    assert len(hub_as_worker) == 0
    assert hub[0]["capabilities"]["attach"] is False
    assert hub[0]["capabilities"]["write"] is False


def test_terminal_targets_mixed_deployment_includes_hub_as_worker_when_permitted(client, admin_auth_header, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "terminal_worker_target_enabled", True)
    monkeypatch.setattr(settings, "terminal_hub_target_enabled", True)
    monkeypatch.setattr(settings, "terminal_hub_as_worker_target_enabled", True)
    monkeypatch.setattr(settings, "hub_can_be_worker", True)

    app_cfg = client.application.config.setdefault("AGENT_CONFIG", {})
    app_cfg["terminal_policy"] = {
        "role_permissions": {
            "admin": [
                "terminal.worker.list",
                "terminal.hub.list",
                "terminal.hub_as_worker.list",
                "terminal.hub_as_worker.create",
                "terminal.hub_as_worker.attach",
                "terminal.hub_as_worker.read",
                "terminal.hub_as_worker.write",
                "terminal.hub_as_worker.kill",
            ]
        }
    }

    agent_repo.save(AgentInfoDB(url="http://worker-b:5001", name="worker-b", role="worker", status="online"))

    res = client.get("/terminal/targets", headers=admin_auth_header)
    assert res.status_code == 200
    payload = res.json["data"]["targets"]

    assert any(item["target_type"] == "worker" for item in payload)
    assert any(item["target_type"] == "hub" for item in payload)
    assert any(item["target_type"] == "hub_as_worker" for item in payload)
