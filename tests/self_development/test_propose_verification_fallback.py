"""A backend that reports finished work must still propose the executable step.

The dispatcher only starts the execute phase when the propose answer carries a
`command` or `tool_calls` (`autopilot_task_dispatcher.py`, `no_executable_step`).
A backend that already performed the change inside the propose step — the opencode
CLI path — answers with an execution report instead, so the run never reached the
verification command and never produced `last_exit_code` / `proposal.command`.

The fallback proposes the verification command the Hub itself declared in the task
description, reusing the prompt markers of the workspace context writer.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services import _task_scoped_propose_persist as mod

MARKER_COMMAND = "python -m pytest -q --confcutdir=tests/self_development -o addopts= tests/self_development"


def _task(description: str) -> SimpleNamespace:
    return SimpleNamespace(description=description, id="task-1")


def test_fallback_returns_the_declared_command_after_the_run_exactly_marker():
    description = (
        "Task requirements ...\n"
        f"Run exactly: {MARKER_COMMAND}\n"
        "Use test.run and finish only after exit code 0."
    )
    assert mod._verification_command_from_task_description(_task(description)) == MARKER_COMMAND


def test_fallback_accepts_the_allowlisted_marker_variant():
    description = f"Run the exact allowlisted command: {MARKER_COMMAND}\nNow wait for the result."
    assert mod._verification_command_from_task_description(_task(description)) == MARKER_COMMAND


def test_fallback_stays_inactive_without_a_marker():
    description = "Do something useful with the workspace, no command declared."
    assert mod._verification_command_from_task_description(_task(description)) is None
    assert mod._verification_command_from_task_description(SimpleNamespace(description=None)) is None
    assert mod._verification_command_from_task_description(None) is None


def test_fallback_collapses_whitespace_inside_the_declared_command():
    description = "Run exactly:   python -m pytest -q   tests/self_development  "
    assert mod._verification_command_from_task_description(_task(description)) == (
        "python -m pytest -q tests/self_development"
    )


class _FakeDeps:
    def build_llm_call_profile_entries(self, **kwargs):
        return []

    def route_response(self, *, data):
        return {"data": data}


class _CapturingExecutionService:
    def __init__(self):
        self.command = None
        self.tool_calls = None

    def persist_task_proposal_result(self, **kwargs):
        self.command = kwargs.get("command")
        self.tool_calls = kwargs.get("tool_calls")
        return {"status": "ok", "command": self.command}


@pytest.fixture()
def app_ctx():
    from flask import Flask

    app = Flask(__name__)
    app.config["AGENT_NAME"] = "probe"
    with app.app_context():
        yield app


def test_persist_proposal_proposes_the_declared_command_when_the_backend_reported_instead(monkeypatch, app_ctx):
    capture = _CapturingExecutionService()
    core = SimpleNamespace(task_execution_service=capture)
    monkeypatch.setattr(mod, "get_core_services", lambda: core)
    monkeypatch.setattr(mod, "_parse_structured_proposal", lambda run: (None, None, None))
    monkeypatch.setattr(mod, "_apply_native_worker_plan", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_proposal_trace", lambda *a, **k: {"trace_id": "trace-1"})
    monkeypatch.setattr(mod, "_proposal_review", lambda *a, **k: {})
    monkeypatch.setattr(mod, "propose_flow_metrics", lambda *a, **k: {})
    monkeypatch.setattr(mod, "_append_session_turn", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_log_terminal_entry", lambda *a, **k: None)
    stages = []
    monkeypatch.setattr(
        mod, "append_stage", lambda pipeline, *, name, status, metadata=None: stages.append((name, metadata))
    )

    run = SimpleNamespace(
        tid="task-1",
        task=_task(f"Verify the change.\nRun exactly: {MARKER_COMMAND}\n"),
        backend_used="opencode",
        proposal_model="ananta-code-27b",
        prompt_for_cli="prompt",
        raw_res="All acceptance criteria verified.",
        latency_ms=1000,
        rc=0,
        cli_err="",
        output_source="stdout",
        routing_reason="default_policy:opencode",
        pipeline={"stages": []},
        repair_meta={"attempted": False, "backend": None, "model": None},
        worker_context_meta={"worker_profile": "balanced"},
        deps=_FakeDeps(),
    )

    result = mod.persist_command_proposal(run, routing={})

    assert capture.command == MARKER_COMMAND
    assert not capture.tool_calls
    parse_stage = [m for (n, m) in stages if n == "parse"]
    assert parse_stage and parse_stage[0]["has_command"] is True
    assert parse_stage[0]["command_source"] == "task_declared_verification_command"
    assert result == {"data": {"status": "ok", "command": MARKER_COMMAND}}


def test_structured_proposal_still_wins_over_the_fallback(monkeypatch, app_ctx):
    capture = _CapturingExecutionService()
    monkeypatch.setattr(mod, "get_core_services", lambda: SimpleNamespace(task_execution_service=capture))
    monkeypatch.setattr(mod, "_parse_structured_proposal", lambda run: ("reason", "echo structured", None))
    monkeypatch.setattr(mod, "_apply_native_worker_plan", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_proposal_trace", lambda *a, **k: {"trace_id": "trace-2"})
    monkeypatch.setattr(mod, "_proposal_review", lambda *a, **k: {})
    monkeypatch.setattr(mod, "propose_flow_metrics", lambda *a, **k: {})
    monkeypatch.setattr(mod, "_append_session_turn", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_log_terminal_entry", lambda *a, **k: None)
    stages = []
    monkeypatch.setattr(
        mod, "append_stage", lambda pipeline, *, name, status, metadata=None: stages.append((name, metadata))
    )

    run = SimpleNamespace(
        tid="task-2",
        task=_task(f"Run exactly: {MARKER_COMMAND}"),
        backend_used="opencode",
        proposal_model="m",
        prompt_for_cli="p",
        raw_res="{}",
        latency_ms=5,
        rc=0,
        cli_err="",
        output_source="stdout",
        routing_reason="r",
        pipeline={"stages": []},
        repair_meta={"attempted": False, "backend": None, "model": None},
        worker_context_meta={},
        deps=_FakeDeps(),
    )

    mod.persist_command_proposal(run, routing={})

    assert capture.command == "echo structured"
    parse_stage = [m for (n, m) in stages if n == "parse"]
    assert parse_stage and parse_stage[0]["command_source"] == "structured_proposal"


def test_fallback_reads_the_description_of_a_mapping_task():
    """The task-scoped propose path hands the task over as a dict, not an object."""
    task = {"description": f"Verify the change.\nRun exactly: {MARKER_COMMAND}\n"}
    assert mod._task_description(task) == task["description"]
    assert mod._verification_command_from_task_description(task) == MARKER_COMMAND


def test_task_description_handles_object_and_missing_shapes():
    assert mod._task_description(_task(f"Run exactly: {MARKER_COMMAND}")) == (
        f"Run exactly: {MARKER_COMMAND}"
    )
    assert mod._task_description({}) == ""
    assert mod._task_description(None) == ""


def test_persist_proposal_falls_back_for_a_mapping_task(monkeypatch, app_ctx):
    capture = _CapturingExecutionService()
    monkeypatch.setattr(mod, "get_core_services", lambda: SimpleNamespace(task_execution_service=capture))
    monkeypatch.setattr(mod, "_parse_structured_proposal", lambda run: (None, None, None))
    monkeypatch.setattr(mod, "_apply_native_worker_plan", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_proposal_trace", lambda *a, **k: {"trace_id": "trace-3"})
    monkeypatch.setattr(mod, "_proposal_review", lambda *a, **k: {})
    monkeypatch.setattr(mod, "propose_flow_metrics", lambda *a, **k: {})
    monkeypatch.setattr(mod, "_append_session_turn", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_log_terminal_entry", lambda *a, **k: None)
    monkeypatch.setattr(mod, "append_stage", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_write_analysis_report_for_proposal", lambda run: None)

    run = SimpleNamespace(
        tid="task-3",
        task={"description": f"Run exactly: {MARKER_COMMAND}"},
        backend_used="opencode",
        proposal_model="m",
        prompt_for_cli="p",
        raw_res="text only, no json",
        latency_ms=5,
        rc=0,
        cli_err="",
        output_source="orchestrator",
        routing_reason="r",
        pipeline={"stages": []},
        repair_meta={"attempted": False, "backend": None, "model": None},
        worker_context_meta={},
        deps=_FakeDeps(),
    )

    mod.persist_command_proposal(run, routing={})
    assert capture.command == MARKER_COMMAND


def test_review_brief_declares_the_verification_command_with_the_marker():
    """`collect_review` compares `proposal.command` against the reserved command.

    The reviewer's propose step must therefore recover that command from its own
    brief exactly the way the development brief declares it — via the
    ``Run exactly:`` marker — otherwise the receipt can never verify.
    """
    from agent.services.self_development.review_workflow import build_review_brief

    brief = build_review_brief(
        todo_id="CMP-701",
        requirements={"acceptance_criteria": ["every criterion passes"]},
        changed_files={"agent/services/text_budget.py": "a" * 64},
        verification_command=MARKER_COMMAND,
    )
    recovered = mod._verification_command_from_task_description(SimpleNamespace(description=brief))
    assert recovered == MARKER_COMMAND, f"review brief must declare the command verbatim, got {recovered!r}"
