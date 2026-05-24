from __future__ import annotations

import time
from types import SimpleNamespace

from agent.services.autopilot_support_service import AutopilotSupportService


class _AgentRepo:
    def __init__(self, agents):
        self._agents = list(agents)

    def get_all(self):
        return list(self._agents)


class _Registry:
    def __init__(self, agents):
        self.agent_repo = _AgentRepo(agents)


def test_available_workers_marks_restart_grace(monkeypatch):
    now = time.time()
    workers = [
        SimpleNamespace(url="http://w-online:5000", role="worker", status="online", registration_validated=True, last_seen=now),
        SimpleNamespace(url="http://w-restart:5000", role="worker", status="degraded", registration_validated=True, last_seen=now - 30),
    ]
    import agent.services.autopilot_support_service as mod

    monkeypatch.setattr(mod, "get_repository_registry", lambda app=None: _Registry(workers))
    service = AutopilotSupportService()
    selected, _ = service.available_workers(
        team_id=None,
        is_worker_circuit_open=lambda _url: False,
        app_config={"AGENT_CONFIG": {"autopilot_worker_policy": {"worker_restart_grace_seconds": 120}}},
        app=None,
    )
    restart = next(w for w in selected if w.url == "http://w-restart:5000")
    assert getattr(restart, "_restart_grace", False) is True
