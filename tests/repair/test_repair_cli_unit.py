"""Unit tests for the repair-script / repair CLI paths in agent/cli_goals.py.

Tests focus on:
- _extract_script_blocks: fenced markdown → clean script
- _host_scan: base + service-specific sections
- _poll_goal_status: terminal detection, detail-fallback
- repair_script_cmd: stdout / --exec / TUI path mocking
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


# ── _extract_script_blocks ────────────────────────────────────────────────────

def test_extract_script_blocks_bash_fence():
    from agent.cli_goals import _extract_script_blocks

    text = "Preamble text.\n\n```bash\napt update\napt install -y curl\n```\n\nTrailing."
    result = _extract_script_blocks(text)
    assert "apt update" in result
    assert "apt install -y curl" in result
    assert "Preamble" not in result
    assert "Trailing" not in result


def test_extract_script_blocks_multiple_fences():
    from agent.cli_goals import _extract_script_blocks

    text = "```sh\necho first\n```\n\n```bash\necho second\n```"
    result = _extract_script_blocks(text)
    assert "echo first" in result
    assert "echo second" in result


def test_extract_script_blocks_fallback_to_plain_lines():
    from agent.cli_goals import _extract_script_blocks

    text = "systemctl restart nginx\ndf -h\n"
    result = _extract_script_blocks(text)
    assert "systemctl restart nginx" in result
    assert "df -h" in result


def test_extract_script_blocks_strips_shebang_in_fallback():
    from agent.cli_goals import _extract_script_blocks

    text = "#!/bin/bash\necho hi\n"
    result = _extract_script_blocks(text)
    assert "#!/bin/bash" not in result
    assert "echo hi" in result


def test_extract_script_blocks_console_fence():
    from agent.cli_goals import _extract_script_blocks

    text = "```console\nls -la\n```"
    result = _extract_script_blocks(text)
    assert "ls -la" in result


# ── _host_scan ────────────────────────────────────────────────────────────────

def test_host_scan_always_includes_base_header():
    from agent.cli_goals import _host_scan

    result = _host_scan("nginx not starting")
    assert "HOST-DIAGNOSE" in result


def test_host_scan_includes_nginx_cmds_for_nginx_topic(monkeypatch):
    from agent.cli_goals import _host_scan

    def _fake_run(cmd: str) -> str:
        return f"output_of: {cmd}"

    monkeypatch.setattr("agent.cli_goals._run_scan_cmd", _fake_run)
    result = _host_scan("nginx crashes on startup")
    assert "nginx" in result.lower()


def test_host_scan_includes_docker_cmds_for_docker_topic(monkeypatch):
    from agent.cli_goals import _host_scan

    def _fake_run(cmd: str) -> str:
        return f"mock: {cmd}"

    monkeypatch.setattr("agent.cli_goals._run_scan_cmd", _fake_run)
    result = _host_scan("docker container keeps restarting")
    assert "docker" in result.lower()


def test_host_scan_respects_max_chars(monkeypatch):
    from agent.cli_goals import _host_scan

    def _fat_run(_cmd: str) -> str:
        return "x" * 2000

    monkeypatch.setattr("agent.cli_goals._run_scan_cmd", _fat_run)
    result = _host_scan("nginx issue", max_chars=500)
    # max_chars limits accumulated section content; headers/labels add a small fixed overhead
    assert len(result) <= 500 + 300  # 500 content limit + fixed header/label overhead


def test_host_scan_gracefully_handles_empty_cmd_output(monkeypatch):
    from agent.cli_goals import _host_scan

    monkeypatch.setattr("agent.cli_goals._run_scan_cmd", lambda _: "")
    result = _host_scan("some problem")
    assert "HOST-DIAGNOSE" in result


# ── _poll_goal_status ─────────────────────────────────────────────────────────

def _mock_response(status_code: int, data: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = {"data": data}
    return r


def test_poll_goal_status_returns_completed_immediately(monkeypatch):
    from agent.cli_goals import _poll_goal_status

    monkeypatch.setattr(
        "agent.cli_goals._request",
        lambda *a, **kw: _mock_response(200, {"status": "completed"}),
    )
    status = _poll_goal_status("goal-123", timeout=10, interval=0)
    assert status == "completed"


def test_poll_goal_status_returns_failed(monkeypatch):
    from agent.cli_goals import _poll_goal_status

    monkeypatch.setattr(
        "agent.cli_goals._request",
        lambda *a, **kw: _mock_response(200, {"status": "failed"}),
    )
    status = _poll_goal_status("goal-fail", timeout=5, interval=0)
    assert status == "failed"


def test_poll_goal_status_detail_fallback_all_tasks_done(monkeypatch):
    """When goal stays 'planned' but all tasks are done, should return 'completed'."""
    from agent.cli_goals import _poll_goal_status

    call_count = {"n": 0}

    def _fake_request(method, path, **kw):
        call_count["n"] += 1
        if "/detail" in path:
            return _mock_response(
                200,
                {
                    "artifacts": {
                        "result_summary": {
                            "task_count": 2,
                            "completed_tasks": 2,
                            "failed_tasks": 0,
                            "cost_summary": {"items": []},
                        }
                    }
                },
            )
        # always return "planned" for the main poll
        return _mock_response(200, {"status": "planned"})

    monkeypatch.setattr("agent.cli_goals._request", _fake_request)
    # interval=0 so it loops quickly; detail checked every 4th poll
    status = _poll_goal_status("goal-detail", timeout=30, interval=0)
    assert status == "completed"


def test_poll_goal_status_timeout(monkeypatch):
    """If goal never reaches terminal state and timeout expires, return 'timeout'."""
    from agent.cli_goals import _poll_goal_status

    monkeypatch.setattr(
        "agent.cli_goals._request",
        lambda *a, **kw: _mock_response(200, {"status": "running"}),
    )
    status = _poll_goal_status("goal-stuck", timeout=1, interval=0)
    assert status == "timeout"


# ── _submit_repair_goal ───────────────────────────────────────────────────────

def test_submit_repair_goal_returns_none_on_goal_creation_failure(monkeypatch):
    from agent.cli_goals import _submit_repair_goal

    def _fail_request(method, path, **kw):
        r = MagicMock()
        r.status_code = 500
        r.json.return_value = {"message": "internal error"}
        r.text = "internal error"
        return r

    monkeypatch.setattr("agent.cli_goals._request", _fail_request)
    result = _submit_repair_goal("nginx broken")
    assert result is None


def test_submit_repair_goal_returns_none_when_goal_fails(monkeypatch):
    from agent.cli_goals import _submit_repair_goal

    create_called = {"n": 0}

    def _fake_request(method, path, **kw):
        if method == "POST":
            return _mock_response(201, {"goal": {"id": "g-fail"}, "created_task_ids": ["t1"]})
        if "/detail" in path:
            return _mock_response(200, {"artifacts": {"artifacts": []}})
        return _mock_response(200, {"status": "failed"})

    monkeypatch.setattr("agent.cli_goals._request", _fake_request)
    result = _submit_repair_goal("broken service", allow_partial=False)
    assert result is None


def test_submit_repair_goal_returns_output_on_success(monkeypatch):
    from agent.cli_goals import _submit_repair_goal

    def _fake_request(method, path, **kw):
        if method == "POST":
            return _mock_response(201, {"goal": {"id": "g-ok"}, "created_task_ids": ["t-ok"]})
        if "/detail" in path and path.endswith("/detail"):
            return _mock_response(
                200,
                {
                    "artifacts": {
                        "artifacts": [
                            {"task_id": "t-ok", "title": "Fix nginx"}
                        ]
                    }
                },
            )
        if path.startswith("/tasks/t-ok"):
            return _mock_response(200, {"last_output": "```bash\nnginx -t\n```"})
        # status poll
        return _mock_response(200, {"status": "completed"})

    monkeypatch.setattr("agent.cli_goals._request", _fake_request)
    result = _submit_repair_goal("nginx broken")
    assert result is not None
    assert len(result) == 1
    assert "nginx -t" in result[0][1]


# ── repair_script_cmd stdout (non-TUI) mode ──────────────────────────────────

def test_repair_script_cmd_prints_script_to_stdout(monkeypatch, capsys):
    from agent.cli_goals import repair_script_cmd

    monkeypatch.setattr(
        "agent.cli_goals._submit_repair_goal",
        lambda *a, **kw: [("Fix nginx", "```bash\nnginx -t\nsystemctl reload nginx\n```")],
    )
    repair_script_cmd("nginx broken")
    captured = capsys.readouterr()
    assert "nginx -t" in captured.out
    assert "systemctl reload nginx" in captured.out


def test_repair_script_cmd_saves_to_file(monkeypatch, tmp_path):
    from agent.cli_goals import repair_script_cmd

    monkeypatch.setattr(
        "agent.cli_goals._submit_repair_goal",
        lambda *a, **kw: [("task", "```bash\napt update\n```")],
    )
    out_file = str(tmp_path / "fix.sh")
    repair_script_cmd("apt issue", script_out=out_file)
    content = open(out_file).read()
    assert "#!/bin/bash" in content
    assert "apt update" in content


def test_repair_script_cmd_exits_1_on_no_output(monkeypatch):
    import sys
    from agent.cli_goals import repair_script_cmd

    monkeypatch.setattr("agent.cli_goals._submit_repair_goal", lambda *a, **kw: None)
    with __import__("pytest").raises(SystemExit) as exc:
        repair_script_cmd("bad thing")
    assert exc.value.code == 1
