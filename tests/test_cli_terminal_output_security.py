from types import SimpleNamespace

import agent.cli_goals as cli_goals


def _response(status_code=500, payload=None, text=""):
    return SimpleNamespace(
        status_code=status_code,
        text=text,
        json=lambda: payload if payload is not None else {},
    )


def test_cli_error_output_strips_terminal_escape_sequences(capsys):
    cli_goals._print_error(_response(payload={"message": "\x1b]8;;https://evil.example\x07click\x1b]8;;\x07\x1b[31mFAIL\x1b[0m"}))

    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "https://evil.example" not in output
    assert "clickFAIL" in output


def test_cli_task_listing_sanitizes_hostile_ids_status_and_titles(monkeypatch, capsys):
    monkeypatch.setattr(
        cli_goals,
        "_request",
        lambda *_args, **_kwargs: _response(
            200,
            [
                {
                    "id": "task-\x1b[2Jspoof",
                    "title": "Deploy\r\x1b[31mFAILED\x1b[0m",
                    "status": "todo\x07",
                }
            ],
        ),
    )

    cli_goals.list_tasks(limit=5)

    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "\x07" not in output
    assert "task-spoof" in output
    assert "Deploy\nFAILED" in output


def test_cli_goal_detail_sanitizes_artifact_preview(monkeypatch, capsys):
    monkeypatch.setattr(
        cli_goals,
        "_request",
        lambda *_args, **_kwargs: _response(
            200,
            {
                "goal": {"id": "goal-1", "status": "completed", "team_id": "team-a"},
                "trace": {"trace_id": "trace-1"},
                "artifacts": {
                    "result_summary": {"task_count": 1, "completed_tasks": 1, "failed_tasks": 0},
                    "headline_artifact": {"preview": "ok\x1b]8;;https://evil.example\x07link\x1b]8;;\x07"},
                },
            },
        ),
    )

    cli_goals.show_goal_detail("goal-1")

    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "https://evil.example" not in output
    assert "oklink" in output
