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

from agent.services.task_scoped import propose_persist as mod

MARKER_COMMAND = "python -m pytest -q --confcutdir=tests/self_development -o addopts= tests/self_development"


def _task(description: str) -> SimpleNamespace:
    return SimpleNamespace(description=description, id="task-1")


def test_fallback_returns_the_declared_command_after_the_run_exactly_marker():
    description = (
        f"Task requirements ...\nRun exactly: {MARKER_COMMAND}\nUse test.run and finish only after exit code 0."
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
    assert mod._task_description(_task(f"Run exactly: {MARKER_COMMAND}")) == (f"Run exactly: {MARKER_COMMAND}")
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


def test_verification_kind_forces_the_lane_runner_fallback():
    """LLM strategies echo the declared command; the opencode runner must still run.

    The adaptive test lane brief contains ``Run exactly:``; without this guard the
    tool-calling strategy returns an executable bash call from a direct LLM turn
    and the orchestrator never reaches its CLI fallback (observed: one 63s llamacpp
    call, no opencode invocation, no report contract).
    """
    from agent.services.task_scoped.propose_orch import _requires_lane_runner

    assert _requires_lane_runner("verification") is True
    assert _requires_lane_runner(" Verification ") is True
    assert _requires_lane_runner("coding") is False
    assert _requires_lane_runner(None) is False


def test_lane_runner_workdir_is_the_carried_snapshot(monkeypatch):
    """The fallback must test in the carried workspace, never in opencode's cwd.

    With workdir=None the runner cannot see the declared ``tests`` root (its cwd
    has none), so the minimum program can only fail with a directory error.
    """
    from types import SimpleNamespace

    import agent.services.task_scoped.propose_orch as propose_orch

    monkeypatch.setattr(
        "agent.services.worker_workspace_service.get_worker_workspace_service",
        lambda: SimpleNamespace(
            resolve_workspace_context=lambda **kw: SimpleNamespace(workspace_dir="/project-workspaces/self-dev-x")
        ),
    )
    assert propose_orch._lane_runner_workdir({"id": "t"}) == "/project-workspaces/self-dev-x"


def test_lane_runner_workdir_fails_bounded_when_unresolvable(monkeypatch):
    from types import SimpleNamespace

    import agent.services.task_scoped.propose_orch as propose_orch

    monkeypatch.setattr(
        "agent.services.worker_workspace_service.get_worker_workspace_service",
        lambda: SimpleNamespace(resolve_workspace_context=lambda **kw: SimpleNamespace(workspace_dir="")),
    )
    import pytest as _pytest

    with _pytest.raises(ValueError, match="lane_workspace_unavailable"):
        propose_orch._lane_runner_workdir({"id": "t"})


def test_lane_runner_workdir_prefers_the_carried_snapshot_over_the_agent_dir(tmp_path, monkeypatch):
    """The per-agent composed dir lacks the carried changes and tests."""
    from types import SimpleNamespace

    import agent.services.task_scoped.propose_orch as propose_orch

    root = tmp_path / "wsroot"
    composed = root / "ananta-worker-1" / "t-1-local"
    composed.mkdir(parents=True)
    carried = root / "self-dev-t-1"
    (carried / "tests").mkdir(parents=True)
    monkeypatch.setattr(
        "agent.services.worker_workspace_service.get_worker_workspace_service",
        lambda: SimpleNamespace(resolve_workspace_context=lambda **kw: SimpleNamespace(workspace_dir=str(composed))),
    )
    assert propose_orch._lane_runner_workdir({"id": "t-1"}) == str(carried)


def _lane_run(*, task_kind_record="verification", inferred_kind="generic", rc=0, answer=None, title=None):
    from types import SimpleNamespace

    task = {"task_kind": task_kind_record}
    if title is not None:
        task["title"] = title
    return SimpleNamespace(
        rc=rc,
        task=task,
        task_kind=inferred_kind,
        raw_res=answer
        if answer is not None
        else '{"passed": true, "ran": ["python -m pytest -q tests"], "failures": []}',
        tid="t-lane-1",
        workspace_dir="/does/not/matter/local",
    )


def test_lane_report_write_uses_the_task_record_kind_not_the_prompt_inference(tmp_path, monkeypatch):
    """The persisted kind is inferred from the prompt and never says verification.

    The artifact must still be written (task record wins) into BOTH candidate
    directories so the collector finds it.
    """
    import agent.services.task_scoped.propose_persist as persist
    from agent.services.self_development.test_fix_loop import read_test_report

    composed = tmp_path / "ananta-worker-2" / "x-local"
    composed.mkdir(parents=True)
    run = _lane_run()
    run.workspace_dir = str(composed)

    persist._write_test_report_for_proposal(run)

    carried = tmp_path / "self-dev-t-lane-1"
    assert read_test_report(carried, task_id="t-lane-1")["passed"] is True
    assert read_test_report(composed, task_id="t-lane-1")["passed"] is True


def test_lane_report_write_skips_non_lane_and_bad_answers(tmp_path):
    import agent.services.task_scoped.propose_persist as persist

    workspace = tmp_path / "ws"
    workspace.mkdir()
    coding = _lane_run(task_kind_record="coding")
    coding.workspace_dir = str(workspace)
    persist._write_test_report_for_proposal(coding)
    failed = _lane_run(rc=1)
    failed.workspace_dir = str(workspace)
    persist._write_test_report_for_proposal(failed)
    prose = _lane_run(answer="Tests passed, all green.")
    prose.workspace_dir = str(workspace)
    persist._write_test_report_for_proposal(prose)

    assert list(workspace.glob("*/test-report.json")) == []
    assert list((workspace / ".ananta").glob("test-report.json")) == [] if (workspace / ".ananta").exists() else True


def test_lane_gate_falls_back_to_title_and_inferred_testing_kind(tmp_path):
    """Each marker alone has proven lossy; any one must still open the gate."""
    import agent.services.task_scoped.propose_persist as persist
    from agent.services.self_development.test_fix_loop import read_test_report

    def composed(name):
        # Production shape: <root>/<agent>/<scope> (four parts) so parents[1] is the root.
        path = tmp_path / "ananta-worker-2" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    # Route task dict without task_kind, but the lane title from either ingest shape.
    run = _lane_run(task_kind_record=None, title="Test lane (retry 1): CMP-9")
    run.workspace_dir = str(composed("t-lane-1-title"))
    persist._write_test_report_for_proposal(run)
    assert read_test_report(tmp_path / "self-dev-t-lane-1", task_id="t-lane-1") is not None

    # Prompt-inferred kind only.
    (tmp_path / "self-dev-t-lane-1" / ".ananta" / "test-report.json").unlink()
    run = _lane_run(task_kind_record=None, inferred_kind="testing")
    run.workspace_dir = str(composed("t-lane-1-inferred"))
    persist._write_test_report_for_proposal(run)
    assert read_test_report(tmp_path / "self-dev-t-lane-1", task_id="t-lane-1") is not None

    # A development task with a non-lane title must stay silent (no artifact).
    (tmp_path / "self-dev-t-lane-1" / ".ananta" / "test-report.json").unlink()
    run = _lane_run(task_kind_record="coding", inferred_kind="coding", title="Fix the add function")
    run.workspace_dir = str(composed("t-lane-1-dev"))
    persist._write_test_report_for_proposal(run)
    assert read_test_report(tmp_path / "self-dev-t-lane-1", task_id="t-lane-1") is None


def test_authoritative_task_kind_prefers_the_queue_record():
    """normalize_task_kind classifies the lane brief as coding; the record wins."""
    from agent.services.task_scoped.propose_orch import _authoritative_task_kind

    assert _authoritative_task_kind({"task_kind": "verification"}, "coding") == "verification"
    assert _authoritative_task_kind({"task_kind": "coding"}, "coding") == "coding"
    assert _authoritative_task_kind({}, "research") == "research"
    assert _authoritative_task_kind(None, "doc") == "doc"


def test_lane_gate_accepts_the_brief_marker_from_the_prompt(tmp_path):
    """The brief is the one marker the propose run is guaranteed to carry."""
    import agent.services.task_scoped.propose_persist as persist
    from agent.services.self_development.test_fix_loop import read_test_report

    workspace = tmp_path / "ananta-worker-2" / "brief"
    workspace.mkdir(parents=True)
    run = _lane_run(task_kind_record=None, inferred_kind="coding")
    run.base_prompt = "Adaptive test lane for TODO CMP-1: ...\nRun exactly: python -m pytest -q tests"
    run.workspace_dir = str(workspace)

    persist._write_test_report_for_proposal(run)

    assert read_test_report(tmp_path / "self-dev-t-lane-1", task_id="t-lane-1")["passed"] is True
