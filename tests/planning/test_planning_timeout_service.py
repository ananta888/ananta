from types import SimpleNamespace

from agent.services.planning_timeout_service import PlanningTimeoutService


class _FakeApp:
    def __init__(self):
        self._ctx = self

    def app_context(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_timeout_service_skips_non_running_goal(monkeypatch):
    svc = PlanningTimeoutService()
    goal = SimpleNamespace(id="g1", status="planned", readiness={})
    repos = SimpleNamespace(
        goal_repo=SimpleNamespace(get_by_id=lambda _gid: goal),
        planning_run_repo=SimpleNamespace(get_by_goal_id=lambda *_args, **_kwargs: []),
    )
    called = {"transition": 0}
    core = SimpleNamespace(goal_lifecycle_service=SimpleNamespace(transition_goal=lambda *a, **k: called.__setitem__("transition", 1)))

    monkeypatch.setattr("agent.services.planning_timeout_service.get_repository_registry", lambda: repos)
    monkeypatch.setattr("agent.services.planning_timeout_service.get_core_services", lambda: core)

    svc._run_once(goal_id="g1", timeout_s=0, app=_FakeApp(), sleep_fn=lambda *_: None)
    assert called["transition"] == 0


def test_timeout_service_transitions_running_goal(monkeypatch):
    svc = PlanningTimeoutService()
    goal = SimpleNamespace(id="g1", status="planning_running", readiness={}, trace_id="t1")
    run = SimpleNamespace(goal_id="g1", status="started")
    repos = SimpleNamespace(
        goal_repo=SimpleNamespace(get_by_id=lambda _gid: goal),
        planning_run_repo=SimpleNamespace(get_by_goal_id=lambda *_args, **_kwargs: [run]),
    )
    called = {"transition": 0}

    def _transition(*_args, **_kwargs):
        called["transition"] += 1

    core = SimpleNamespace(goal_lifecycle_service=SimpleNamespace(transition_goal=_transition))
    monkeypatch.setattr("agent.services.planning_timeout_service.get_repository_registry", lambda: repos)
    monkeypatch.setattr("agent.services.planning_timeout_service.get_core_services", lambda: core)
    monkeypatch.setattr("agent.services.planning_timeout_service.get_planning_telemetry_service", lambda: SimpleNamespace(update_run=lambda *a, **k: None))
    monkeypatch.setattr("agent.services.planning_timeout_service.record_product_event", lambda *a, **k: None)

    svc._run_once(goal_id="g1", timeout_s=0, app=_FakeApp(), sleep_fn=lambda *_: None)
    assert called["transition"] == 1


def test_timeout_service_singleflight_per_goal():
    svc = PlanningTimeoutService()
    assert svc._acquire("g1") is True
    assert svc._acquire("g1") is False
    svc._release("g1")
    assert svc._acquire("g1") is True


def test_timeout_service_does_not_start_background_thread_in_testing_app():
    svc = PlanningTimeoutService()
    app = SimpleNamespace(testing=True, config={"AGENT_CONFIG": {"planning_policy": {}}})

    assert svc.start_deadline_guard(goal_id="g1", timeout_s=300, app=app) is False
    assert svc._active == {}
