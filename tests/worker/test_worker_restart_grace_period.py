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
        SimpleNamespace(
            url="http://w-online:5000", role="worker", status="online", registration_validated=True, last_seen=now
        ),
        SimpleNamespace(
            url="http://w-restart:5000",
            role="worker",
            status="degraded",
            registration_validated=True,
            last_seen=now - 30,
        ),
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


def _worker(url: str, now: float):
    return SimpleNamespace(url=url, role="worker", status="online", registration_validated=True, last_seen=now)


def test_available_workers_drops_foreign_loopback_phantoms(monkeypatch):
    now = time.time()
    workers = [
        _worker("http://w-real:5000", now),
        _worker("http://localhost:5000", now),
        _worker("http://127.0.0.1:5000", now),
        _worker("http://[::1]:5000", now),
    ]
    import agent.services.autopilot_support_service as mod

    monkeypatch.setattr(mod, "get_repository_registry", lambda app=None: _Registry(workers))
    monkeypatch.setattr(mod.settings, "agent_url", "http://ai-agent-hub:5000", raising=False)
    service = AutopilotSupportService()
    selected, online_count = service.available_workers(
        team_id=None,
        is_worker_circuit_open=lambda _url: False,
        app_config={},
        app=None,
    )
    urls = [w.url for w in selected]
    assert urls == ["http://w-real:5000"]
    assert online_count == 1


def test_available_workers_keeps_own_loopback_url(monkeypatch):
    now = time.time()
    workers = [_worker("http://localhost:5000", now), _worker("http://w-real:5000", now)]
    import agent.services.autopilot_support_service as mod

    monkeypatch.setattr(mod, "get_repository_registry", lambda app=None: _Registry(workers))
    monkeypatch.setattr(mod.settings, "agent_url", "http://localhost:5000", raising=False)
    service = AutopilotSupportService()
    selected, online_count = service.available_workers(
        team_id=None,
        is_worker_circuit_open=lambda _url: False,
        app_config={},
        app=None,
    )
    urls = [w.url for w in selected]
    assert "http://localhost:5000" in urls
    assert "http://w-real:5000" in urls
    assert online_count == 2
