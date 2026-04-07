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


def test_select_model_for_task_prefers_team_role_template(monkeypatch):
    class _RoleRepo:
        @staticmethod
        def get_by_id(_role_id):
            return SimpleNamespace(name="Developer", default_template_id="tpl-role")

    class _TeamRepo:
        @staticmethod
        def get_by_id(_team_id):
            return SimpleNamespace(name="OpenCode Team", role_templates={"role-1": "tpl-team"})

    class _TemplateRepo:
        @staticmethod
        def get_by_id(template_id):
            mapping = {
                "tpl-role": SimpleNamespace(name="Classic Scrum Developer"),
                "tpl-team": SimpleNamespace(name="OpenCode Scrum - Developer"),
            }
            return mapping.get(template_id)

    repos = SimpleNamespace(
        team_member_repo=SimpleNamespace(get_by_team=lambda _team_id: []),
        team_repo=_TeamRepo(),
        role_repo=_RoleRepo(),
        template_repo=_TemplateRepo(),
    )
    monkeypatch.setattr("agent.routes.tasks.autopilot_tick_engine.get_repository_registry", lambda: repos)
    monkeypatch.setattr(
        "agent.routes.tasks.autopilot_tick_engine.recommend_model_for_context",
        lambda **kwargs: {"model": None, "selection_source": "benchmark_context_learning"},
    )

    loop = SimpleNamespace(
        _agent_config=lambda: {
            "adaptive_model_routing_enabled": False,
            "template_model_overrides": {"opencode scrum - developer": "opencode-model"},
        },
        _app=SimpleNamespace(config={"DATA_DIR": "data"}),
    )
    task = SimpleNamespace(
        assigned_role_id="role-1",
        team_id="team-1",
        assigned_agent_url="",
        task_kind="coding",
    )

    selected, meta = _select_model_for_task(loop=loop, task=task)
    assert selected == "opencode-model"
    assert meta["source"] == "template_model_overrides:name"
    assert meta["template_name"] == "OpenCode Scrum - Developer"


def test_select_model_for_task_normalizes_legacy_ollama_benchmark_model(monkeypatch):
    repos = SimpleNamespace(
        team_member_repo=SimpleNamespace(get_by_team=lambda _team_id: []),
        role_repo=SimpleNamespace(get_by_id=lambda _role_id: SimpleNamespace(name="Developer", default_template_id=None)),
        template_repo=SimpleNamespace(get_by_id=lambda _template_id: None),
    )
    monkeypatch.setattr("agent.routes.tasks.autopilot_tick_engine.get_repository_registry", lambda: repos)
    monkeypatch.setattr(
        "agent.routes.tasks.autopilot_tick_engine.recommend_model_for_context",
        lambda **kwargs: {"model": "ananta-default", "selection_source": "benchmark_context_learning"},
    )

    loop = SimpleNamespace(
        _agent_config=lambda: {
            "default_provider": "ollama",
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
    assert selected == "qwen2.5-coder:7b"
    assert meta["source"] == "benchmark_context_learning"
