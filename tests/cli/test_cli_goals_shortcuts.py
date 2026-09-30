import sys
from types import SimpleNamespace

import pytest

import agent.cli_goals as cli


def test_cli_shortcuts_cover_product_entry_commands():
    assert {"ask", "plan", "analyze", "review", "diagnose", "patch", "new-project", "evolve-project", "repair-admin"}.issubset(
        cli.SHORTCUT_GOALS
    )
    assert cli.SHORTCUT_GOALS["review"]["mode"] == "code_review"
    assert cli.SHORTCUT_GOALS["diagnose"]["mode"] == "docker_compose_repair"
    assert cli.SHORTCUT_GOALS["new-project"]["mode"] == "new_software_project"
    assert cli.SHORTCUT_GOALS["evolve-project"]["mode"] == "project_evolution"
    assert cli.SHORTCUT_GOALS["repair-admin"]["mode"] == "admin_repair"
    assert cli.SHORTCUT_GOALS["ask"]["mode"] is None
    assert cli.SHORTCUT_GOALS["plan"]["mode"] is None


def _capturing_deps(captured: list[dict]) -> cli.CliGoalsDependencies:
    def fake_transport(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            status_code=201,
            json=lambda: {"data": {"goal": {"id": "goal-1", "status": "planned"}, "created_task_ids": ["task-1"]}},
            text="",
        )

    return cli.CliGoalsDependencies(
        request=cli.HubHttpClient(
            base_url_provider=lambda: "http://hub:5000",
            token_provider=lambda base_url: "token",
            transport=fake_transport,
        )
    )


@pytest.mark.parametrize(
    ("shortcut", "mode"),
    [
        ("ask", None),
        ("plan", None),
        ("analyze", "repo_analysis"),
        ("review", "code_review"),
        ("diagnose", "docker_compose_repair"),
        ("patch", "code_fix"),
        ("new-project", "new_software_project"),
        ("evolve-project", "project_evolution"),
        ("repair-admin", "admin_repair"),
    ],
)
def test_submit_shortcut_maps_to_goal_model(shortcut, mode):
    calls: list[dict] = []

    result = cli.submit_shortcut(
        shortcut, "check login flow", team_id="team-a", create_tasks=True, deps=_capturing_deps(calls)
    )

    captured = calls[0]["json"]
    assert result == ["task-1"]
    assert calls[0]["url"] == "http://hub:5000/goals"
    assert captured.get("mode") == mode
    assert captured["team_id"] == "team-a"
    assert captured["create_tasks"] is True
    assert captured["mode_data"]["shortcut"] == shortcut
    assert captured["mode_data"]["shortcut_text"] == "check login flow"
    assert "check login flow" in captured["goal"]
    assert "Kurzkommando" in captured["context"]


def test_product_shortcuts_pass_structured_mode_data():
    calls: list[dict] = []
    deps = _capturing_deps(calls)

    cli.submit_shortcut("new-project", "Release-Check-Tool bauen", deps=deps)
    assert calls[-1]["json"]["mode"] == "new_software_project"
    assert calls[-1]["json"]["mode_data"]["project_idea"] == "Release-Check-Tool bauen"

    cli.submit_shortcut("evolve-project", "Dashboard erweitern", deps=deps)
    assert calls[-1]["json"]["mode"] == "project_evolution"
    assert calls[-1]["json"]["mode_data"]["change_goal"] == "Dashboard erweitern"

    cli.submit_shortcut("repair-admin", "Service restart loop", deps=deps)
    assert calls[-1]["json"]["mode"] == "admin_repair"
    assert calls[-1]["json"]["mode_data"]["issue_symptom"] == "Service restart loop"
    assert calls[-1]["json"]["mode_data"]["dry_run"] is True


def test_main_routes_shortcut_words_to_submit_shortcut(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(sys, "argv", ["cli_goals", "review", "auth", "changes", "--team", "team-a"])

    cli.main(deps=_capturing_deps(calls))

    assert len(calls) == 1
    payload = calls[0]["json"]
    assert payload["mode"] == "code_review"
    assert payload["mode_data"]["shortcut_text"] == "auth changes"
    assert payload["team_id"] == "team-a"
    assert payload["create_tasks"] is True


def test_main_routes_first_run_to_guidance(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cli_goals", "--first-run"])

    cli.main(deps=cli.CliGoalsDependencies(base_url=lambda: "http://hub:5000"))

    out = capsys.readouterr().out
    assert "Ananta CLI First Run" in out
    assert "http://hub:5000" in out
    assert "ananta status" in out
    assert "Success signal:" in out
    assert "ANANTA_BASE_URL" in out


def test_main_requires_text_for_shortcut(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cli_goals", "diagnose"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert "needs a short description" in capsys.readouterr().out


def test_get_auth_token_exits_with_clear_error_when_login_fails(monkeypatch, capsys):
    monkeypatch.setenv("ANANTA_USER", "admin")
    monkeypatch.setenv("ANANTA_PASSWORD", "wrong")
    monkeypatch.setattr(
        cli.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(status_code=401, json=lambda: {}, text="unauthorized"),
    )

    with pytest.raises(SystemExit) as exc:
        cli.get_auth_token("http://hub:5000")

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Login failed - 401" in out
    assert "ANANTA_USER/ANANTA_PASSWORD" in out


def test_request_uses_base_url_env_and_bearer_token(monkeypatch):
    captured = {}
    monkeypatch.setenv("ANANTA_BASE_URL", "http://hub.example/")

    def fake_request(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {"data": {"ok": True}}, text="")

    request = cli.HubHttpClient(token_provider=lambda base_url: f"token-for-{base_url}", transport=fake_request)

    response = request("GET", "/goals", params={"limit": 2}, timeout=7)

    assert response.status_code == 200
    assert captured["url"] == "http://hub.example/goals"
    assert captured["headers"] == {"Authorization": "Bearer token-for-http://hub.example"}
    assert captured["params"] == {"limit": 2}
    assert captured["timeout"] == 7
