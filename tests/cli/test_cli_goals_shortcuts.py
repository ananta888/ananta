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
def test_submit_shortcut_maps_to_goal_model(monkeypatch, shortcut, mode):
    captured = {}

    def fake_submit_goal(**kwargs):
        captured.update(kwargs)
        return ["task-1"]

    monkeypatch.setattr(cli, "submit_goal", fake_submit_goal)

    result = cli.submit_shortcut(shortcut, "check login flow", team_id="team-a", create_tasks=True)

    assert result == ["task-1"]
    assert captured["mode"] == mode
    assert captured["team_id"] == "team-a"
    assert captured["create_tasks"] is True
    assert captured["mode_data"]["shortcut"] == shortcut
    assert captured["mode_data"]["shortcut_text"] == "check login flow"
    assert "check login flow" in captured["goal"]
    assert "Kurzkommando" in captured["context"]


def test_product_shortcuts_pass_structured_mode_data(monkeypatch):
    captured = {}

    def fake_submit_goal(**kwargs):
        captured.update(kwargs)
        return ["task-1"]

    monkeypatch.setattr(cli, "submit_goal", fake_submit_goal)

    cli.submit_shortcut("new-project", "Release-Check-Tool bauen")
    assert captured["mode"] == "new_software_project"
    assert captured["mode_data"]["project_idea"] == "Release-Check-Tool bauen"

    cli.submit_shortcut("evolve-project", "Dashboard erweitern")
    assert captured["mode"] == "project_evolution"
    assert captured["mode_data"]["change_goal"] == "Dashboard erweitern"

    cli.submit_shortcut("repair-admin", "Service restart loop")
    assert captured["mode"] == "admin_repair"
    assert captured["mode_data"]["issue_symptom"] == "Service restart loop"
    assert captured["mode_data"]["dry_run"] is True


def test_main_routes_shortcut_words_to_submit_shortcut(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["cli_goals", "review", "auth", "changes", "--team", "team-a"])
    monkeypatch.setattr(cli, "submit_shortcut", lambda *args, **kwargs: calls.append((args, kwargs)) or [])

    cli.main()

    assert calls == [(("review", "auth changes"), {"team_id": "team-a", "create_tasks": True})]


def test_main_routes_first_run_to_guidance(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cli_goals", "--first-run"])
    monkeypatch.setattr(cli, "get_base_url", lambda: "http://hub:5000")

    cli.main()

    out = capsys.readouterr().out
    assert "Ananta CLI First Run" in out
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
    monkeypatch.setattr(cli, "get_auth_token", lambda base_url: f"token-for-{base_url}")

    def fake_request(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {"data": {"ok": True}}, text="")

    monkeypatch.setattr(cli.requests, "request", fake_request)

    response = cli._request("GET", "/goals", params={"limit": 2}, timeout=7)

    assert response.status_code == 200
    assert captured["url"] == "http://hub.example/goals"
    assert captured["headers"] == {"Authorization": "Bearer token-for-http://hub.example"}
    assert captured["params"] == {"limit": 2}
    assert captured["timeout"] == 7
