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
