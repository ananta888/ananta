"""Analysis report producer for backends that drive CodeCompass over MCP.

The writer must never invent evidence: observations have to match the
revision-bound baseline from the task description, an existing report (the
mutation loop's) is never overwritten, and the payload is validated against the
gate's schema before it reaches the workspace.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.self_development import analysis_report as mod

SHA_A = "a" * 64
SHA_B = "b" * 64

DESCRIPTION = (
    "Task requirements ...\n"
    'Revision-bound source context (JSON data):\n'
    + json.dumps(
        {
            "revision": "60bb49a41",
            "files": [
                {"path": "agent/services/example.py", "sha256": SHA_A, "content": "print(1)\n", "start_line": 1},
                {"path": "agent/services/broken.py", "sha256": "nope", "content": "x", "start_line": 1},
            ],
        }
    )
    + "\nOnly change these files: agent/services/example.py\n"
)


@pytest.fixture(autouse=True)
def clean_recorder():
    mod.reset_observations()
    yield
    mod.reset_observations()


def _ok_result(*, digest: str | None = "b" * 64) -> dict:
    result = {
        "status": "ok",
        "evidence": [
            {"kind": "file_excerpt", "path": "agent/services/example.py", "line_start": 3, "line_end": 9},
            {"kind": "file_excerpt", "path": "no_lines_here", "excerpt": "unrelated"},
        ],
    }
    if digest is not None:
        result["data"] = {"file_sha256": digest}
    return result


def test_first_observation_of_a_path_wins():
    mod.record_observation(task_id="t1", path="agent/services/example.py", line_start=1, line_end=4)
    mod.record_observation(task_id="t1", path="agent/services/example.py", line_start=50, line_end=60)

    with mod._LOCK:
        entry = mod._OBSERVATIONS["t1"]["agent/services/example.py"]
    assert (entry["line_start"], entry["line_end"]) == (1, 4)


def test_record_tool_result_keeps_digest_and_skips_incomplete_entries():
    mod.record_tool_result(_ok_result(digest=SHA_B), task_id="t1", workspace="/tmp")
    with mod._LOCK:
        recorded = mod._OBSERVATIONS["t1"]
    assert list(recorded) == ["agent/services/example.py"]
    assert recorded["agent/services/example.py"]["file_sha256"] == SHA_B
    assert recorded["agent/services/example.py"]["line_start"] == 3


def test_record_tool_result_ignores_errors_and_bad_ranges():
    mod.record_tool_result({"status": "error", "error": "boom"}, task_id="t1", workspace="/tmp")
    mod.record_tool_result(
        {"status": "ok", "evidence": [{"path": "x.py", "line_start": 0, "line_end": 5}]},
        task_id="t1",
        workspace="/tmp",
    )
    mod.record_tool_result(None, task_id="t1", workspace="/tmp")
    with mod._LOCK:
        assert "t1" not in mod._OBSERVATIONS


def test_record_tool_result_computes_the_digest_when_the_payload_has_none(tmp_path):
    import hashlib

    target = tmp_path / "svc.py"
    target.write_text("print(1)\n", encoding="utf-8")
    expected = hashlib.sha256(target.read_bytes()).hexdigest()

    mod.record_tool_result(
        {"status": "ok", "evidence": [{"path": "svc.py", "line_start": 1, "line_end": 2}]},
        task_id="t1",
        workspace=tmp_path,
    )
    with mod._LOCK:
        assert mod._OBSERVATIONS["t1"]["svc.py"]["file_sha256"] == expected


def test_baseline_parser_reads_the_source_bundle_and_survives_garbage():
    assert mod.baseline_from_description(DESCRIPTION) == {"agent/services/example.py": SHA_A}
    assert mod.baseline_from_description("no bundle here") == {}
    assert mod.baseline_from_description("Revision-bound source context (JSON data):\nnot json\nOnly change these files: x") == {}


def test_baseline_trusted_observations_filters_swapped_digests():
    mod.record_observation(task_id="t1", path="agent/services/example.py", line_start=1, line_end=2, file_sha256=SHA_A)
    mod.record_observation(task_id="t1", path="agent/services/changed.py", line_start=1, line_end=2, file_sha256=SHA_B)
    trusted = mod.baseline_trusted_observations("t1", {"agent/services/example.py": SHA_A})
    assert [entry["path"] for entry in trusted] == ["agent/services/example.py"]


def test_writer_refuses_when_a_report_already_exists(tmp_path):
    workspace = tmp_path / "ws"
    existing = workspace / ".ananta" / "mutation-report.json"
    existing.parent.mkdir(parents=True)
    existing.write_text('{"schema": "ananta_worker_mutation_report.v1", "own": "loop"}', encoding="utf-8")

    assert mod.write_analysis_report(workspace, task_id="t1", description=DESCRIPTION, answer="done") is False
    assert json.loads(existing.read_text())["own"] == "loop"


def test_writer_without_a_baseline_refuses(tmp_path):
    workspace = tmp_path / "ws"
    # An observation can only be trusted against the revision-bound baseline in
    # the description; without that bundle there is nothing to bind it to.
    mod.record_observation(task_id="t1", path="agent/services/example.py", line_start=1, line_end=2, file_sha256=SHA_A)
    assert mod.write_analysis_report(workspace, task_id="t1", description="plain task", answer="done") is False
    assert not (workspace / ".ananta" / "mutation-report.json").exists()


def test_writer_drops_a_digest_that_misses_the_baseline(tmp_path):
    """A recorded digest that misses the baseline never reaches the report.

    Without a baseline-trusted observation there is nothing the gateway could
    verify, so the writer refuses instead of falling back to weaker evidence.
    """
    workspace = tmp_path / "ws"
    mod.reset_observations("t1")
    mod.record_observation(task_id="t1", path="agent/services/example.py", line_start=1, line_end=2, file_sha256=SHA_B)
    assert mod.write_analysis_report(workspace, task_id="t1", description=DESCRIPTION, answer="done") is False
    assert not (workspace / ".ananta" / "mutation-report.json").exists()


def test_writer_refuses_without_an_answer(tmp_path):
    workspace = tmp_path / "ws"
    mod.record_observation(task_id="t1", path="agent/services/example.py", line_start=1, line_end=2, file_sha256=SHA_A)
    assert mod.write_analysis_report(workspace, task_id="t1", description=DESCRIPTION, answer="   ") is False
    assert not (workspace / ".ananta" / "mutation-report.json").exists()


def test_writer_produces_a_report_the_gate_can_validate(tmp_path):
    from agent.services.self_development.analysis_verification import WorkerAnalysisReport

    workspace = tmp_path / "ws"
    mod.record_observation(task_id="t1", path="agent/services/example.py", line_start=3, line_end=9, file_sha256=SHA_A)

    assert mod.write_analysis_report(workspace, task_id="t1", description=DESCRIPTION, answer="Verified: done") is True

    payload = json.loads((workspace / ".ananta" / "mutation-report.json").read_text(encoding="utf-8"))
    # The gate reads JSON text: under strict models a Python list is not a tuple,
    # so validating the dict directly would reject what the gateway accepts.
    report = WorkerAnalysisReport.model_validate_json(json.dumps(payload))
    assert report.task_id == "t1"
    assert report.outcome == "final_answer"
    observed = report.code_analysis.observed_sources
    assert [(entry.path, entry.line_start, entry.line_end) for entry in observed] == [
        ("agent/services/example.py", 3, 9)
    ]
    assert report.code_analysis.analysis.sources == observed
    assert "Verified: done" in report.code_analysis.analysis.findings
    assert report.code_analysis.codecompass_observed is True


def test_verify_worker_analysis_accepts_the_written_report(tmp_path):
    from agent.services.self_development.analysis_verification import verify_worker_analysis

    workspace = tmp_path / "ws"
    baseline = {"agent/services/example.py": SHA_A}
    mod.record_observation(task_id="t1", path="agent/services/example.py", line_start=3, line_end=9, file_sha256=SHA_A)
    assert mod.write_analysis_report(workspace, task_id="t1", description=DESCRIPTION, answer="done") is True

    report = verify_worker_analysis(workspace, task_id="t1", baseline=baseline)
    assert report.task_id == "t1"


def test_propose_hook_writes_only_after_a_successful_propose(monkeypatch):
    from agent.services import _task_scoped_propose_persist as persist_mod
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(
        "agent.services.self_development.analysis_report.write_analysis_report",
        lambda workspace, *, task_id, description, answer: calls.append((str(workspace), task_id)) or True,
    )
    run = SimpleNamespace(workspace_dir="/ws", tid="t1", task=SimpleNamespace(description=DESCRIPTION),
                          raw_res="done", rc=0)
    persist_mod._write_analysis_report_for_proposal(run)
    assert calls == [("/ws", "t1")]

    calls.clear()
    run.rc = 1
    persist_mod._write_analysis_report_for_proposal(run)
    assert calls == []


def test_mcp_record_hook_is_best_effort(monkeypatch):
    from agent.cli_backends import opencode_codecompass_mcp as mcp_mod

    seen = []
    monkeypatch.setattr(
        "agent.services.self_development.analysis_report.record_tool_result",
        lambda result, *, task_id, workspace: seen.append((task_id, workspace)),
    )
    mcp_mod._record_observation({"status": "ok"}, task_id="t1", workspace="/ws")
    assert seen == [("t1", "/ws")]

    def explode(*args, **kwargs):
        raise RuntimeError("recorder down")

    monkeypatch.setattr("agent.services.self_development.analysis_report.record_tool_result", explode)
    mcp_mod._record_observation({"status": "ok"}, task_id="t1", workspace="/ws")  # must not raise
    mcp_mod._record_observation({"status": "ok"}, task_id="", workspace="/ws")  # nothing to record


def test_task_brief_carries_the_assignment_instruction_in_both_modes(monkeypatch):
    """The brief embeds the assignment as its source of truth — both names included.

    The observation instruction lives in the task description (the assignment), so
    the brief must carry it verbatim into both execution modes: the loop reads its
    own registry name, opencode its MCP name, and the brief must not collapse them
    into one namespace (that made the loop call tools its policy does not know).
    """
    from types import SimpleNamespace

    from agent.services._worker_workspace_context_writer import _task_brief_content

    monkeypatch.setattr(
        "agent.services._worker_workspace_context_writer._allowlisted_test_commands",
        lambda: ["python -m pytest -q tests/self_development"],
    )
    profile = SimpleNamespace(profile_id="feature", is_fallback=False)
    task = {"id": "CMP-701", "title": "Add helper", "description": "Do the thing"}
    assignment = (
        "Required before any write, in this order: "
        '(1) call CodeCompass for what you plan to change — on the ananta-worker loop the tool is '
        '`codecompass.plan_context` with {"query": "<what changes and why>"}; on the opencode path the MCP '
        "equivalent is `ananta_codecompass_codecompass_plan_context` with the same argument. "
        "(2) fetch the authoritative excerpts with "
        '`codecompass.get_file_context` (loop) or `ananta_codecompass_codecompass_get_file_context` '
        '(opencode), arguments {"paths": [<files you change>], "line_ranges": [{path, line_start, line_end}]}. '
        "Do not read those files with your own read tools: the Hub binds exactly these CodeCompass ranges "
        "to this task and blocks publication without them."
    )

    for include_contract in (True, False):
        brief = _task_brief_content(
            task,
            base_prompt=f"Add a helper and verify it.\n{assignment}",
            active_profile=profile,
            include_response_contract=include_contract,
            char_limit=None,
        )
        assert "codecompass.plan_context" in brief, "brief must carry the loop tool name"
        assert "ananta_codecompass_codecompass_plan_context" in brief, (
            "brief must carry the verbatim MCP tool name opencode invokes"
        )
        assert "line_ranges" in brief, "brief must carry the arguments, not only the names"
        assert "publication" in brief, "brief must state why the call matters"
        if include_contract:
            assert "response-contract.md" in brief
        else:
            assert "No JSON response is required" in brief


