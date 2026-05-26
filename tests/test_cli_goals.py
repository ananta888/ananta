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


def test_submit_goal_prints_first_run_success_signal(monkeypatch, capsys):
    monkeypatch.setattr(cli_goals, "get_auth_token", lambda base_url: "token")
    monkeypatch.setattr(cli_goals, "get_base_url", lambda: "http://localhost:5000")

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=30):
        return _FakeResponse(
            201,
            {
                "data": {
                    "goal": {"id": "goal-1", "goal": json["goal"], "status": "planned"},
                    "created_task_ids": ["task-1"],
                }
            },
        )

    monkeypatch.setattr(cli_goals.requests, "request", _fake_request)

    cli_goals.submit_goal(goal="first run")

    out = capsys.readouterr().out
    assert "Goal ID: goal-1" in out
    assert "Status: planned" in out
    assert "Tasks created: 1" in out
    assert "Next step: ananta goal --goal-detail goal-1" in out
    assert "Success signal:" in out


def test_submit_goal_prints_reference_profile_visibility(monkeypatch, capsys):
    monkeypatch.setattr(cli_goals, "get_auth_token", lambda base_url: "token")
    monkeypatch.setattr(cli_goals, "get_base_url", lambda: "http://localhost:5000")

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=30):
        return _FakeResponse(
            201,
            {
                "data": {
                    "goal": {
                        "id": "goal-ref-1",
                        "goal": json["goal"],
                        "status": "planned",
                        "reference_profile": {
                            "profile_id": "ref.python.ananta_backend",
                            "fit_level": "high_fit",
                            "reason_summary": "ref.python.ananta_backend selected via language_exact",
                        },
                    },
                    "created_task_ids": ["task-ref-1"],
                }
            },
        )

    monkeypatch.setattr(cli_goals.requests, "request", _fake_request)

    cli_goals.submit_goal(goal="reference visible")

    out = capsys.readouterr().out
    assert "Reference profile: ref.python.ananta_backend" in out
    assert "Reference fit: high_fit" in out
    assert "Reference reason:" in out


def test_shortcut_review_submits_goal_with_review_mode(monkeypatch):
    calls: list[dict] = []

    monkeypatch.setattr(cli_goals, "get_auth_token", lambda base_url: "token")
    monkeypatch.setattr(cli_goals, "get_base_url", lambda: "http://localhost:5000")

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=30):
        calls.append({"method": method, "url": url, "json": json})
        return _FakeResponse(
            201,
            {
                "data": {
                    "goal": {"id": "goal-review", "goal": json["goal"], "status": "planned"},
                    "created_task_ids": ["task-review"],
                }
            },
        )

    monkeypatch.setattr(cli_goals.requests, "request", _fake_request)
    created = cli_goals.submit_shortcut("review", "Pruefe die Login-Aenderungen")

    assert created == ["task-review"]
    payload = calls[0]["json"]
    assert payload["mode"] == "code_review"
    assert payload["mode_data"]["shortcut"] == "review"
    assert payload["mode_data"]["scope"] == "Pruefe die Login-Aenderungen"
    assert "Pruefe die Login-Aenderungen" in payload["goal"]


def test_shortcut_repair_admin_submits_goal_with_admin_repair_mode(monkeypatch):
    calls: list[dict] = []

    monkeypatch.setattr(cli_goals, "get_auth_token", lambda base_url: "token")
    monkeypatch.setattr(cli_goals, "get_base_url", lambda: "http://localhost:5000")

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=30):
        calls.append({"method": method, "url": url, "json": json})
        return _FakeResponse(
            201,
            {
                "data": {
                    "goal": {"id": "goal-repair-admin", "goal": json["goal"], "status": "planned"},
                    "created_task_ids": ["task-repair-admin"],
                }
            },
        )

    monkeypatch.setattr(cli_goals.requests, "request", _fake_request)
    created = cli_goals.submit_shortcut("repair-admin", "Service restart loop")

    assert created == ["task-repair-admin"]
    payload = calls[0]["json"]
    assert payload["mode"] == "admin_repair"
    assert payload["mode_data"]["issue_symptom"] == "Service restart loop"
    assert payload["mode_data"]["execution_scope"] == "bounded_repair"
    assert payload["mode_data"]["dry_run"] is True


def test_resolve_output_dir_bare_name():
    container, host = cli_goals._resolve_output_dir("fibonacci")
    assert container == "/project-workspaces/fibonacci"
    assert host == "./project-workspaces/fibonacci"


def test_resolve_output_dir_relative_dotslash():
    container, host = cli_goals._resolve_output_dir("./myproject")
    assert container == "/project-workspaces/myproject"
    assert host == "./project-workspaces/myproject"


def test_resolve_output_dir_already_absolute_container():
    container, host = cli_goals._resolve_output_dir("/project-workspaces/fibonacci")
    assert container == "/project-workspaces/fibonacci"
    assert host == "./project-workspaces/fibonacci"


def test_resolve_output_dir_arbitrary_absolute_maps_to_external_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    requested = tmp_path / "out" / "fib"
    container, host = cli_goals._resolve_output_dir(str(requested))
    assert container.startswith("/project-workspaces/external/")
    assert host == str(requested)
    assert requested.is_symlink()


def test_parse_rag_sources_bare_collection_ids():
    result = cli_goals._parse_rag_sources("col-1,col-2")
    assert result["knowledge_collection_ids"] == ["col-1", "col-2"]
    assert "artifact_ids" not in result
    assert "repo_scope_refs" not in result


def test_parse_rag_sources_mixed_prefixes():
    result = cli_goals._parse_rag_sources("col:coll-abc,art:artifact-xyz,path:agent/services")
    assert result["knowledge_collection_ids"] == ["coll-abc"]
    assert result["artifact_ids"] == ["artifact-xyz"]
    assert result["repo_scope_refs"] == [{"path": "agent/services"}]


def test_parse_rag_sources_empty_returns_empty():
    assert cli_goals._parse_rag_sources("") == {}
    assert cli_goals._parse_rag_sources("  ") == {}


def test_purge_goal_calls_delete_endpoint(monkeypatch, capsys):
    calls: list[dict] = []

    monkeypatch.setattr(cli_goals, "get_auth_token", lambda base_url: "token")
    monkeypatch.setattr(cli_goals, "get_base_url", lambda: "http://localhost:5000")

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=30):
        calls.append({"method": method, "url": url, "params": params})
        return _FakeResponse(
            200,
            {
                "data": {
                    "goal_id": "goal-1",
                    "deleted_total": 5,
                    "prompt_traces_deleted": 2,
                    "deleted": {"goal": 1, "tasks": 2},
                }
            },
        )

    monkeypatch.setattr(cli_goals.requests, "request", _fake_request)
    rc = cli_goals.purge_goal("goal-1")
    out = capsys.readouterr().out
    assert rc == 0
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"] == "http://localhost:5000/goals/goal-1/purge"
    assert calls[0]["params"] == {"include_prompt_traces": "1"}
    assert "Goal purged: goal-1" in out


def test_main_goal_purge_requires_yes(monkeypatch, capsys):
    monkeypatch.setattr(cli_goals, "get_auth_token", lambda base_url: "token")
    monkeypatch.setattr(cli_goals, "get_base_url", lambda: "http://localhost:5000")
    try:
        cli_goals.main(["--goal-purge", "goal-1"])
    except SystemExit as exc:
        assert int(exc.code) == 2
    captured = capsys.readouterr()
    err = captured.out + captured.err
    assert "--yes" in err


def test_submit_goal_passes_rag_sources_in_execution_preferences(monkeypatch):
    calls: list[dict] = []

    monkeypatch.setattr(cli_goals, "get_auth_token", lambda base_url: "token")
    monkeypatch.setattr(cli_goals, "get_base_url", lambda: "http://localhost:5000")

    def _fake_request(method, url, headers=None, json=None, params=None, timeout=30):
        calls.append({"method": method, "url": url, "json": json})
        return _FakeResponse(
            201,
            {"data": {"goal": {"id": "goal-rag", "goal": json["goal"], "status": "planned"}, "created_task_ids": []}},
        )

    monkeypatch.setattr(cli_goals.requests, "request", _fake_request)
    cli_goals.submit_goal(goal="add feature", rag_sources="col:my-collection,art:my-artifact")

    payload = calls[0]["json"]
    rag = payload["execution_preferences"]["rag_sources"]
    assert rag["knowledge_collection_ids"] == ["my-collection"]
    assert rag["artifact_ids"] == ["my-artifact"]


def test_sources_bootstrap_command_dry_run(monkeypatch, capsys, tmp_path):
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    try:
        cli_goals.main(["sources", "bootstrap", "ananta-dev-default", "--dry-run", "--skip-source", "wikimedia-wikipedia-initial-dump"])
    except SystemExit as exc:
        assert int(exc.code) == 0
    out = capsys.readouterr().out
    assert "status: planned" in out
    assert "source_pack_id: ananta-dev-default" in out
    assert "skipped: wikimedia-wikipedia-initial-dump" in out


def test_sources_bootstrap_command_writes_bundle(monkeypatch, capsys, tmp_path):
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    try:
        cli_goals.main(["sources", "bootstrap", "ananta-dev-default", "--skip-source", "wikimedia-wikipedia-initial-dump"])
    except SystemExit as exc:
        assert int(exc.code) == 0
    out = capsys.readouterr().out
    assert "status: ok" in out
    assert "bundle_id:" in out
    assert "bundle_path:" in out
