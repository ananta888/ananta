from agent import cli_goals


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_list_modes_calls_goal_modes_endpoint(monkeypatch, capsys):
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(cli_goals, "get_auth_token", lambda base_url: "token")
    monkeypatch.setattr(cli_goals, "get_base_url", lambda: "http://localhost:5000")

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=30):
        calls.append((method, url))
        return _FakeResponse(
            200,
            {
                "data": [
                    {"id": "code_fix", "title": "Codeproblem loesen"},
                    {"id": "docker_compose_repair", "title": "Docker-/Compose-Reparatur"},
                ]
            },
        )

    monkeypatch.setattr(cli_goals.requests, "request", _fake_request)
    cli_goals.list_modes()

    out = capsys.readouterr().out
    assert ("GET", "http://localhost:5000/goals/modes") in calls
    assert "Goal modes (2):" in out
    assert "docker_compose_repair" in out


def test_submit_goal_posts_to_goal_endpoint_with_mode(monkeypatch):
    calls: list[dict] = []

    monkeypatch.setattr(cli_goals, "get_auth_token", lambda base_url: "token")
    monkeypatch.setattr(cli_goals, "get_base_url", lambda: "http://localhost:5000")

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=30):
        calls.append({"method": method, "url": url, "json": json})
        return _FakeResponse(
            201,
            {
                "data": {
                    "goal": {"id": "goal-1", "goal": "repair", "status": "planned"},
                    "created_task_ids": ["task-1", "task-2"],
                }
            },
        )

    monkeypatch.setattr(cli_goals.requests, "request", _fake_request)
    created = cli_goals.submit_goal(
        goal="repair",
        context="ctx",
        team_id="team-a",
        create_tasks=True,
        mode="docker_compose_repair",
        mode_data={"service": "hub"},
    )

    assert created == ["task-1", "task-2"]
    assert calls
    request_payload = calls[0]["json"]
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "http://localhost:5000/goals"
    assert request_payload["mode"] == "docker_compose_repair"
    assert request_payload["mode_data"] == {"service": "hub"}
