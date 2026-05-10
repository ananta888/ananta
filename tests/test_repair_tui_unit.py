"""Unit tests for agent/repair_tui.py — command extraction and retry context."""
from __future__ import annotations

from agent.repair_tui import (
    RepairCommand,
    RepairTuiResult,
    _build_failure_summary,
    build_retry_context,
    extract_commands_from_outputs,
)


# ── extract_commands_from_outputs ─────────────────────────────────────────────

def test_extract_bash_block_single_command():
    outputs = [("task-1", "```bash\nsystemctl restart nginx\n```")]
    cmds = extract_commands_from_outputs(outputs)
    assert len(cmds) == 1
    assert cmds[0].command == "systemctl restart nginx"
    assert cmds[0].source_task == "task-1"


def test_extract_sh_block_multi_command():
    outputs = [("task-a", "```sh\napt update\napt install -y curl\n```")]
    cmds = extract_commands_from_outputs(outputs)
    assert [c.command for c in cmds] == ["apt update", "apt install -y curl"]


def test_extract_skips_comment_lines():
    outputs = [("t", "```bash\n# This is a comment\necho hello\n```")]
    cmds = extract_commands_from_outputs(outputs)
    assert len(cmds) == 1
    assert cmds[0].command == "echo hello"


def test_extract_skips_bang_lines():
    outputs = [("t", "```bash\n!/bin/bash\necho world\n```")]
    cmds = extract_commands_from_outputs(outputs)
    assert len(cmds) == 1
    assert cmds[0].command == "echo world"


def test_extract_deduplicates_across_tasks():
    outputs = [
        ("task-1", "```bash\necho dup\n```"),
        ("task-2", "```bash\necho dup\necho unique\n```"),
    ]
    cmds = extract_commands_from_outputs(outputs)
    commands = [c.command for c in cmds]
    assert commands.count("echo dup") == 1
    assert "echo unique" in commands


def test_extract_falls_back_to_command_line_format():
    outputs = [("t", "command=df -h")]
    cmds = extract_commands_from_outputs(outputs)
    assert len(cmds) == 1
    assert cmds[0].command == "df -h"


def test_extract_returns_empty_for_prose_only():
    outputs = [("t", "There are no shell commands here. Just prose.")]
    cmds = extract_commands_from_outputs(outputs)
    assert cmds == []


def test_extract_multiple_blocks_in_one_output():
    out = "```bash\necho first\n```\n\nSome text\n\n```sh\necho second\n```"
    cmds = extract_commands_from_outputs([("t", out)])
    assert [c.command for c in cmds] == ["echo first", "echo second"]


def test_extract_shell_block_variant():
    outputs = [("t", "```shell\nls -la\n```")]
    cmds = extract_commands_from_outputs(outputs)
    assert cmds[0].command == "ls -la"


def test_extract_blank_lines_in_block_are_skipped():
    outputs = [("t", "```bash\n\necho hi\n\n```")]
    cmds = extract_commands_from_outputs(outputs)
    assert len(cmds) == 1
    assert cmds[0].command == "echo hi"


# ── build_retry_context ───────────────────────────────────────────────────────

def test_build_retry_context_empty_history():
    assert build_retry_context([]) == ""


def test_build_retry_context_single_iteration():
    cmd = RepairCommand(command="systemctl restart nginx", source_task="t1")
    cmd.exit_code = 0
    cmd.executed = True
    result = RepairTuiResult(executed=[cmd], verdict="retry", failure_summary="")
    ctx = build_retry_context([result])
    assert "Versuch 1" in ctx
    assert "systemctl restart nginx" in ctx
    assert "OK" in ctx


def test_build_retry_context_failed_command():
    cmd = RepairCommand(command="apt install foo", source_task="t2")
    cmd.exit_code = 1
    cmd.stderr = "E: Unable to locate package foo"
    cmd.executed = True
    result = RepairTuiResult(executed=[cmd], verdict="retry", failure_summary="apt install foo → exit=1")
    ctx = build_retry_context([result])
    assert "FEHLER" in ctx
    assert "apt install foo" in ctx
    assert "Zusammenfassung" in ctx


def test_build_retry_context_multiple_iterations():
    def _make_result(i: int, ok: bool) -> RepairTuiResult:
        cmd = RepairCommand(command=f"cmd-{i}", source_task="t")
        cmd.exit_code = 0 if ok else 1
        cmd.executed = True
        return RepairTuiResult(executed=[cmd], verdict="retry")

    history = [_make_result(1, ok=False), _make_result(2, ok=True)]
    ctx = build_retry_context(history)
    assert "Versuch 1" in ctx
    assert "Versuch 2" in ctx
    assert "cmd-1" in ctx
    assert "cmd-2" in ctx


# ── RepairCommand properties ──────────────────────────────────────────────────

def test_repair_command_succeeded_property():
    cmd = RepairCommand(command="echo ok")
    cmd.executed = True
    cmd.exit_code = 0
    assert cmd.succeeded is True
    assert cmd.failed is False


def test_repair_command_failed_property():
    cmd = RepairCommand(command="false")
    cmd.executed = True
    cmd.exit_code = 1
    assert cmd.failed is True
    assert cmd.succeeded is False


def test_repair_command_not_executed_is_neither():
    cmd = RepairCommand(command="echo")
    assert cmd.succeeded is False
    assert cmd.failed is False


# ── _build_failure_summary ────────────────────────────────────────────────────

def test_build_failure_summary_only_includes_failed():
    ok_cmd = RepairCommand(command="echo ok")
    ok_cmd.executed = True
    ok_cmd.exit_code = 0

    fail_cmd = RepairCommand(command="exit 2")
    fail_cmd.executed = True
    fail_cmd.exit_code = 2
    fail_cmd.stderr = "bad exit"

    summary = _build_failure_summary([ok_cmd, fail_cmd])
    assert "exit 2" in summary
    assert "echo ok" not in summary


def test_build_failure_summary_empty_on_all_success():
    cmd = RepairCommand(command="echo hi")
    cmd.executed = True
    cmd.exit_code = 0
    assert _build_failure_summary([cmd]) == ""


def test_build_failure_summary_multiple_failures():
    failed = []
    for i in range(3):
        c = RepairCommand(command=f"cmd-{i}")
        c.executed = True
        c.exit_code = i + 1
        c.stderr = f"err-{i}"
        failed.append(c)
    summary = _build_failure_summary(failed)
    for i in range(3):
        assert f"cmd-{i}" in summary
