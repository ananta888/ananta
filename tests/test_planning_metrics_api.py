from agent.ai_agent import create_app


def test_metrics_grouped_by_model(monkeypatch):
    app = create_app()
    app.config["TESTING"] = True

    from agent.routes.admin import planning_metrics as mod

    monkeypatch.setattr(mod, "check_auth", lambda f: f)
    monkeypatch.setattr(mod, "admin_required", lambda f: f)
    monkeypatch.setattr(
        mod,
        "get_planning_metrics_service",
        lambda: type("S", (), {"summarize": lambda self, **_: {"run_count": 1, "groups": [{"group": "lmstudio::gemma", "run_count": 1}]}})(),
    )

    client = app.test_client()
    resp = client.get("/admin/planning/metrics")
    assert resp.status_code == 200
    data = resp.get_json().get("data")
    assert data["run_count"] == 1


def test_model_benchmark_planning_scores(monkeypatch):
    from agent.services.ollama_benchmark_service import OllamaBenchmarkService
    import agent.services.ollama_benchmark_service as mod

    class _Run:
        def __init__(self):
            self.model_name = "test123"
            self.model_provider = "lmstudio"
            self.mode_data = {"benchmark_scenario": "simple_project"}
            self.parse_mode = "strict_json"
            self.repair_needed = False
            self.expected_artifacts_count = 1
            self.verification_spec_count = 1
            self.dependency_mode_distribution = {"parallel": 1}
            self.id = "r1"

    class _Eval:
        total_score = 0.8

    class _EvalRepo:
        def get_by_run_id(self, _):
            return _Eval()

    class _RunRepo:
        def get_recent(self, limit=1000):
            return [_Run()]

    class _Reg:
        planning_run_repo = _RunRepo()
        planning_evaluation_repo = _EvalRepo()

    monkeypatch.setattr(mod, "get_repository_registry", lambda: _Reg())
    out = OllamaBenchmarkService().summarize_planning_quality(model="test123", provider="lmstudio")
    assert out["average_plan_score"] == 0.8
