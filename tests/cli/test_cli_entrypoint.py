"""Smoke tests for the ananta CLI entrypoint and domain dispatch."""
from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

from agent.cli.main import main


def _run(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    rc = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            result = main(list(argv))
        rc = 0 if result is None else int(result)
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Top-level help
# ---------------------------------------------------------------------------

def test_no_args_exits_0():
    rc, out, _ = _run([])
    assert rc == 0
    assert "ananta" in out.lower() or "usage" in out.lower()


def test_help_flag_exits_0():
    rc, _, _ = _run(["--help"])
    assert rc == 0


def test_unknown_command_exits_nonzero():
    rc, _, err = _run(["nonexistent-command-xyz"])
    assert rc != 0


# ---------------------------------------------------------------------------
# Domain group dispatching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", [
    "config", "runtime", "llm", "hub", "worker",
    "goal", "task", "project", "rag", "repair", "dev",
])
def test_domain_help_via_main(domain):
    """ananta <domain> --help dispatches correctly and exits 0."""
    rc, out, err = _run([domain, "--help"])
    combined = out + err
    assert rc == 0, f"'ananta {domain} --help' via main() exited {rc}. Output:\n{combined}"
    assert len(combined.strip()) > 0


# ---------------------------------------------------------------------------
# Flat command backward-compat
# ---------------------------------------------------------------------------

def test_doctor_help():
    rc, out, err = _run(["doctor", "--help"])
    assert rc == 0
    assert "doctor" in (out + err).lower()


def test_update_help():
    rc, out, err = _run(["update", "--help"])
    assert rc == 0


def test_init_help():
    rc, out, err = _run(["init", "--help"])
    assert rc == 0


def test_prompt_help_via_main():
    rc, out, err = _run(["prompt", "--help"])
    assert rc == 0
    combined = out + err
    assert "inspect" in combined or "goal" in combined


def test_task_help_via_main():
    rc, out, err = _run(["task", "--help"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Exit code contract
# ---------------------------------------------------------------------------

def test_unknown_command_exits_2():
    rc, _, _ = _run(["completely-unknown-xyz"])
    assert rc == 2


def test_help_always_exits_0():
    """Verify that --help on each domain group returns 0, not 1."""
    domains = ["config", "runtime", "llm", "hub", "worker", "goal", "task",
               "project", "rag", "repair", "prompt", "dev"]
    for domain in domains:
        rc, _, _ = _run([domain, "--help"])
        assert rc == 0, f"'ananta {domain} --help' returned {rc}, expected 0"


# ---------------------------------------------------------------------------
# Domain subcommand help via main
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["config", "show", "--help"],
    ["config", "setup-planning", "--help"],
    ["config", "validate", "--help"],
    ["goal", "create", "--help"],
    ["goal", "list", "--help"],
    ["goal", "inspect", "--help"],
    ["goal", "status", "--help"],
    ["goal", "ask", "--help"],
    ["goal", "new-project", "--help"],
    ["task", "inspect", "--help"],
    ["task", "list", "--help"],
    ["dev", "acceptance", "--help"],
    ["dev", "check", "--help"],
    ["dev", "audit", "--help"],
    ["dev", "smoke", "--help"],
    ["dev", "benchmark", "--help"],
    ["hub", "status", "--help"],
    ["worker", "list", "--help"],
    ["llm", "list", "--help"],
    ["llm", "log", "--help"],
    ["runtime", "list", "--help"],
    ["runtime", "inspect", "--help"],
    ["prompt", "inspect", "--help"],
    ["prompt", "goal-traces", "--help"],
])
def test_subcommand_help(argv):
    rc, out, err = _run(argv)
    combined = out + err
    assert rc == 0, f"'{' '.join(argv)}' exited {rc}. Output:\n{combined}"
    assert len(combined.strip()) > 0
