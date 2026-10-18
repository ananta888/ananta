"""Runtime prerequisites for source analysis, including all mutation entry points."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.cli_backends.workspace_mutation.analysis import CodeAnalysisGate
from agent.cli_backends.workspace_mutation import handlers, tool_actions


def _session(tmp_path, required=True):
    evidence = []
    return SimpleNamespace(
        analysis_gate=CodeAnalysisGate(required=required, workspace=tmp_path),
        cfg={"require_code_analysis": required}, workspace=tmp_path,
        mode="controlled_workspace", task_id=None, task_prompt="fix source",
        materialization_manifest={}, read_only_streak=20,
        count_file_attempt=lambda path: 1, limits=SimpleNamespace(max_attempts_per_file=3),
        next_tool_call_id=lambda prefix: "call", add_evidence=evidence.append,
        evidence_blocks=evidence,
        services=SimpleNamespace(tool_policy=SimpleNamespace(evaluate=lambda **kw:
            SimpleNamespace(allowed=True, decision="allow"))),
        finish_with_summary=lambda outcome, summary, err: (1, json.dumps(summary), err),
    )


def _turn(message):
    return SimpleNamespace(message=message, row={}, iteration=1, err="", out="")


@pytest.mark.parametrize("message", [
    {"kind": "workspace_write", "files": [{"path": "new.py", "content": "bad"}]},
    {"kind": "patch_request", "target_path": "new.py", "variant": "write_file_create_only", "content": "bad"},
    {"kind": "tool_request", "tool_name": "repo.write_file", "arguments": {"path": "new.py", "content": "bad"}},
    {"kind": "tool_request", "tool_name": "repo.apply_patch", "arguments": {"target_path": "new.py"}},
])
def test_mutations_are_blocked_before_analysis(tmp_path, message, monkeypatch):
    # Given a workspace with no observed analysis and permissive downstream policy.
    session = _session(tmp_path)
    monkeypatch.setattr(tool_actions, "_route_read_tool", lambda s, t, n, a: (n, a))
    monkeypatch.setattr(tool_actions, "execute_ananta_tool", lambda **kw: pytest.fail("write executed"))
    monkeypatch.setattr(handlers, "_write_one_file", lambda *args: pytest.fail("direct write executed"))
    # When any mutation entry point is requested.
    handlers.ACTION_HANDLERS[message["kind"]](session, _turn(message))
    # Then the prerequisite blocks it before filesystem or tool execution.
    assert session.evidence_blocks[-1]["error"] == "code_analysis_required"
    assert not (tmp_path / "new.py").exists()


def test_final_answer_cannot_claim_success_without_analysis(tmp_path):
    # Given a session lacking research.
    session = _session(tmp_path)
    # When the model declares success.
    result = handlers.handle_final_answer(session, _turn({"kind": "final_answer", "answer": "done"}))
    # Then it terminates with a bounded prerequisite failure.
    assert result[0] == 1
    assert json.loads(result[1])["reason"] == "code_analysis_required"


def _analysis(path: Path):
    return {"sources": [{"path": path.name, "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "line_start": 1, "line_end": 1}],
            "findings": "Function returns a constant.", "change_reasoning": "Return configured value.",
            "test_reasoning": "Assert two distinct configured values."}


@pytest.mark.parametrize("codecompass_ok", [False, True])
def test_claimed_reads_do_not_unlock_analysis(tmp_path, codecompass_ok):
    # Given an existing file, but no runtime materialized source range.
    path = tmp_path / "app.py"
    path.write_text("value = 1\n")
    gate = CodeAnalysisGate(required=True, workspace=tmp_path)
    gate.observe_codecompass(successful=codecompass_ok)
    # When the model claims it inspected that file.
    reason = gate.accept(_analysis(path))
    # Then the model claim alone cannot unlock mutation.
    assert reason is not None
    assert gate.pending


def test_analysis_accepts_only_observed_current_source_bytes(tmp_path):
    # Given a successful CodeCompass lookup and runtime source range.
    path = tmp_path / "app.py"
    path.write_text("value = 1\n")
    gate = CodeAnalysisGate(required=True, workspace=tmp_path)
    gate.observe_codecompass(successful=True)
    analysis = _analysis(path)
    gate.observe_range(**analysis["sources"][0])
    # When the model provides source-bound change and test reasoning.
    reason = gate.accept(analysis)
    # Then mutation is unlocked and the source hash remains auditable.
    assert reason is None
    assert not gate.pending
    assert gate.state()["analysis"]["sources"] == analysis["sources"]


def test_source_change_between_read_and_analysis_is_rejected(tmp_path):
    # Given observed source bytes that subsequently changed.
    path = tmp_path / "app.py"
    path.write_text("value = 1\n")
    gate = CodeAnalysisGate(required=True, workspace=tmp_path)
    gate.observe_codecompass(successful=True)
    analysis = _analysis(path)
    gate.observe_range(**analysis["sources"][0])
    path.write_text("value = 2\n")
    # When submitting analysis of the stale snapshot.
    reason = gate.accept(analysis)
    # Then that snapshot cannot authorize writes.
    assert reason == "code_analysis_source_changed"
    assert gate.pending


@pytest.mark.parametrize("status", ["ok", "error"])
def test_codecompass_materialization_is_observed_only_after_success(tmp_path, monkeypatch, status):
    # Given real source ranges and a CodeCompass adapter that succeeds or fails.
    from agent.services.tools.repo_tools import repo_read_file_range

    path = tmp_path / "app.py"
    path.write_text("value = 1\n")
    session = _session(tmp_path)
    calls = []

    def execute(**kw):
        calls.append(kw["tool_name"])
        if kw["tool_name"] == "repo.read_file_range":
            return repo_read_file_range(workspace_dir=kw["workspace_dir"], arguments=kw["arguments"],
                                        tool_call_id=kw["tool_call_id"])
        return {"status": status, "data": {"context_bundle": {"location_refs": [
            {"path": "app.py", "line_start": 1, "line_end": 1}]}}}

    monkeypatch.setattr(tool_actions, "_route_read_tool", lambda s, t, n, a: (n, a))
    monkeypatch.setattr(tool_actions, "execute_ananta_tool", execute)
    # When a CodeCompass request runs despite the old force-action read threshold.
    tool_actions.handle_tool_request(session, _turn({"tool_name": "codecompass.plan_context", "arguments": {}}))
    reason = session.analysis_gate.accept(_analysis(path))
    # Then only successful navigation with actual source materialization unlocks the gate.
    assert (reason is None) == (status == "ok")
    assert ("repo.read_file_range" in calls) == (status == "ok")


@pytest.mark.parametrize("config", [
    {"require_code_analysis": True, "enabled": False},
    {"require_code_analysis": True, "enabled": True, "resolved_mode": "read_only"},
    {"require_code_analysis": True, "enabled": True, "resolved_mode": "controlled_workspace"},
])
def test_required_analysis_never_falls_back_to_weaker_loop(tmp_path, monkeypatch, config):
    # Given a required analysis configuration and an unavailable mutation loop.
    from agent.cli_backends import sgpt, workspace_mutation, tool_loop

    monkeypatch.setattr(sgpt, "_get_agent_config", lambda: {"ananta_worker_workspace_mutation": config})
    monkeypatch.setattr(workspace_mutation, "get_workspace_mutation_config", lambda workdir: config)

    class LoopUnavailable(RuntimeError):
        """The configured mutation runtime failed during setup."""

    def failed_loop(*args, **kwargs):
        raise LoopUnavailable

    monkeypatch.setattr(workspace_mutation, "run_ananta_worker_workspace_mutation", failed_loop)
    monkeypatch.setattr(tool_loop, "get_tool_loop_config", lambda: pytest.fail("unsafe fallback"))
    # When iterative execution starts.
    result = sgpt._run_ananta_worker_iterative("fix", str(tmp_path), options=[], timeout=1, model=None)
    # Then the run fails within the guarded boundary.
    assert result[0] == 1
    assert json.loads(result[1])["reason"].startswith("required_code_analysis_loop_")


@pytest.mark.parametrize("with_analysis", [False, True])
def test_real_mutation_loop_requires_analysis_before_applying_source(tmp_path, monkeypatch, with_analysis):
    # Given an actual materialized workspace and deterministic CodeCompass transport.
    from agent.cli_backends.workspace_mutation import run_ananta_worker_workspace_mutation
    from agent.services.codecompass_task_capability import task_capability_scope
    from agent.services.tools.repo_tools import repo_read_file_range

    source = tmp_path / "app.py"
    source.write_text("value = 1\n")
    metadata = tmp_path / ".ananta"
    metadata.mkdir()
    (metadata / "materialization-manifest.json").write_text(json.dumps([
        {"workspace_path": "app.py", "allowed_operations": ["read", "write", "patch"]}]))
    original_execute = tool_actions.execute_ananta_tool

    def execute(**kw):
        if kw["tool_name"] == "codecompass.plan_context":
            return {"status": "ok", "data": {"context_bundle": {"location_refs": [
                {"path": "app.py", "line_start": 1, "line_end": 1}]}}}
        if kw["tool_name"] == "repo.read_file_range":
            return repo_read_file_range(workspace_dir=kw["workspace_dir"], arguments=kw["arguments"],
                                        tool_call_id=kw["tool_call_id"])
        return original_execute(**kw)

    monkeypatch.setattr(tool_actions, "execute_ananta_tool", execute)
    messages = []
    if with_analysis:
        messages += [{"kind": "tool_request", "tool_name": "codecompass.plan_context", "arguments": {}},
                     {"kind": "code_analysis", **_analysis(source)}]
    messages += [{"kind": "workspace_write", "files": [{"path": "app.py", "content": "value = 2\n"}]}]
    if with_analysis:
        messages += [{"kind": "final_answer", "answer": "changed"}]
    outputs = iter(messages)

    def llm(**kw):
        return 0, json.dumps(next(outputs)), ""

    # When the real bounded feedback loop receives the actions.
    with task_capability_scope(None, task_id="scoped-task"):
        result = run_ananta_worker_workspace_mutation(
            "Change configured value", str(tmp_path), options=[], timeout=1, model=None,
            llm_runner=llm, config={"enabled": True, "resolved_mode": "controlled_workspace",
                                  "require_code_analysis": True, "max_feedback_iterations": len(messages),
                                  "require_materialized_scope": True, "max_diff_chars": 1000},
        )
    # Then only the researched action sequence can change source and finish successfully.
    assert result[0] == (0 if with_analysis else 1)
    assert source.read_text() == ("value = 2\n" if with_analysis else "value = 1\n")
    report = json.loads((metadata / "mutation-report.json").read_text())
    assert report["task_id"] == "scoped-task"
    assert report["code_analysis"]["status"] == ("accepted" if with_analysis else "required")
    if not with_analysis:
        assert json.loads(result[1])["reason"] == "max_iterations"


def test_missing_source_read_names_an_existing_file_in_the_directory(tmp_path, monkeypatch):
    # Given an existing test module beside the path the model guessed.
    session = _session(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_execution.py").write_text("def test_gate():\n    return True\n")
    monkeypatch.setattr(tool_actions, "_route_read_tool", lambda s, t, n, a: (n, a))
    monkeypatch.setattr(tool_actions, "execute_ananta_tool",
                        lambda **kw: {"status": "error", "error": "file_not_found", "data": {}})
    # When the read names a file that does not exist.
    tool_actions.handle_tool_request(session, _turn({
        "tool_name": "repo.read_file_range",
        "arguments": {"path": "tests/test_agent_live_gate.py", "line_start": 1, "line_end": 40}}))
    # Then the answer points at a real source file instead of a bare error.
    data = session.evidence_blocks[-1]["data"]
    assert data["action_required"] == "read_one_of_the_existing_source_paths_instead"
    assert data["existing_paths"] == ["tests/test_execution.py"]


def test_successful_document_read_is_flagged_while_analysis_is_pending(tmp_path, monkeypatch):
    # Given a required analysis gate that observes source files only.
    session = _session(tmp_path)
    monkeypatch.setattr(tool_actions, "_route_read_tool", lambda s, t, n, a: (n, a))
    monkeypatch.setattr(tool_actions, "execute_ananta_tool",
                        lambda **kw: {"status": "ok", "data": {"file_sha256": "0" * 64}})
    # When the model reads a document instead of source code.
    tool_actions.handle_tool_request(session, _turn({
        "tool_name": "repo.read_file_range",
        "arguments": {"path": "docs/releases/native-core-gate-buzz-independent.md",
                      "line_start": 1, "line_end": 45}}))
    # Then the gate states what it needs to make progress.
    assert session.evidence_blocks[-1]["data"]["action_required"] == "analysis_requires_a_source_file_range"


def test_read_of_a_path_outside_any_existing_directory_names_the_next_tool(tmp_path, monkeypatch):
    # Given a guessed path whose parent directory does not exist either.
    session = _session(tmp_path)
    monkeypatch.setattr(tool_actions, "_route_read_tool", lambda s, t, n, a: (n, a))
    monkeypatch.setattr(tool_actions, "execute_ananta_tool",
                        lambda **kw: {"status": "error", "error": "file_not_found", "data": {}})
    # When the read cannot be resolved at all.
    tool_actions.handle_tool_request(session, _turn({
        "tool_name": "repo.read_file_range",
        "arguments": {"path": "missing/module.py", "line_start": 1, "line_end": 20}}))
    # Then the bounded answer names the discovery tool.
    assert session.evidence_blocks[-1]["data"]["action_required"] == "path_does_not_exist_use_repo_list_files"


def test_analysis_rejection_carries_an_action_required_hint(tmp_path):
    # Given an existing source file and a sequence of rejected analyses.
    path = tmp_path / "app.py"
    path.write_text("value = 1\n")
    other = tmp_path / "other.py"
    other.write_text("other = 1\n")
    session = _session(tmp_path)
    # When no CodeCompass observation exists yet.
    handlers.handle_code_analysis(session, _turn({"kind": "code_analysis", **_analysis(path)}))
    assert session.evidence_blocks[-1]["data"]["action_required"] == "call_codecompass_first"
    # When CodeCompass was seen but no source range was observed.
    session.analysis_gate.observe_codecompass(successful=True)
    handlers.handle_code_analysis(session, _turn({"kind": "code_analysis", **_analysis(path)}))
    assert session.evidence_blocks[-1]["data"]["action_required"] == "read_a_source_file_range_first"
    # When a range was observed but the analysis cites a different file.
    session.analysis_gate.observe_range(**_analysis(path)["sources"][0])
    handlers.handle_code_analysis(session, _turn({"kind": "code_analysis", **_analysis(other)}))
    assert (session.evidence_blocks[-1]["data"]["action_required"]
            == "cite_an_observed_source_range_with_its_sha256")
