from types import SimpleNamespace

from agent.routes.tasks.autopilot_tick_engine import _select_model_for_task


def test_select_model_for_task_uses_adaptive_benchmark_when_no_static_override(monkeypatch):
    class _RoleRepo:
        @staticmethod
        def get_by_id(_role_id):
            return SimpleNamespace(name="Frontend Developer", default_template_id="tpl-ui")

    class _TemplateRepo:
        @staticmethod
        def get_by_id(_template_id):
            return SimpleNamespace(name="UI Template")

    repos = SimpleNamespace(
        team_member_repo=SimpleNamespace(get_by_team=lambda _team_id: []),
        role_repo=_RoleRepo(),
        template_repo=_TemplateRepo(),
    )
    monkeypatch.setattr("agent.routes.tasks.autopilot_tick_engine.get_repository_registry", lambda: repos)
    monkeypatch.setattr(
        "agent.routes.tasks.autopilot_tick_engine.recommend_model_for_context",
        lambda **kwargs: {"model": "learned-model", "selection_source": "benchmark_context_learning"},
    )

    loop = SimpleNamespace(
        _agent_config=lambda: {
            "adaptive_model_routing_enabled": True,
            "adaptive_model_routing_min_samples": 2,
        },
        _app=SimpleNamespace(config={"DATA_DIR": "data"}),
    )
    task = SimpleNamespace(
        assigned_role_id="role-1",
        team_id="team-1",
        assigned_agent_url="http://worker:5000",
        task_kind="coding",
    )

    selected, meta = _select_model_for_task(loop=loop, task=task)
    assert selected == "learned-model"
    assert meta["source"] == "benchmark_context_learning"
    assert meta["role_name"] == "Frontend Developer"
    assert meta["template_name"] == "UI Template"

