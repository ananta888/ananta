import threading
import time
from types import SimpleNamespace

from agent.services.agent_health_monitor_service import AgentHealthMonitorService
from agent.services.agent_registry_service import AgentRegistryService


class _FakeRepo:
    def __init__(self, agent):
        self._agent = agent
        self._saved = []

    def get_all(self):
        return [self._agent]

    def save(self, agent):
        self._saved.append(agent)


class _FakeApp:
    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def app_context(self):
        return self._Ctx()


def test_health_monitor_keeps_busy_worker_not_offline(monkeypatch):
    agent = SimpleNamespace(name="w1", url="http://w1", status="online", last_seen=time.time(), token=None)
    repo = _FakeRepo(agent)

    monkeypatch.setattr(
        "agent.services.agent_health_monitor_service.get_repository_registry",
        lambda: SimpleNamespace(agent_repo=repo),
    )
    monkeypatch.setattr(
        "agent.services.agent_health_monitor_service.get_task_execution_tracking_service",
        lambda: SimpleNamespace(reconcile_worker_executions=lambda now: {}),
    )

    svc = AgentHealthMonitorService()
    monkeypatch.setattr(svc, "_check_agent", lambda _agent: (_agent, ("busy", None)))

    svc.check_all_agents_health(
        app=_FakeApp(),
        failure_state={},
        failure_lock=threading.Lock(),
        offline_failure_threshold=3,
    )

    assert agent.status == "busy"


def test_registry_marks_busy_when_capacity_reached():
    svc = AgentRegistryService()
    agent = SimpleNamespace(
        name="w1",
        url="http://w1",
        role="worker",
        worker_roles=["coding"],
        capabilities=["execute"],
        runtime_targets=[],
        execution_limits={"max_parallel_tasks": 2, "current_load": 2},
        status="online",
        registration_validated=True,
        validation_errors=[],
        last_seen=time.time(),
    )

    entry = svc.build_directory_entry(agent=agent, timeout=300, now=time.time())
    assert entry["status"] == "busy"
    assert entry["available_for_routing"] is False
