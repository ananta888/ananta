"""AWWPI-014/015/016/017/018: feedback iteration loop tests.

The loop under test is the closed feedback cycle: worker action ->
DiffResult/PolicyResult/TestResult -> evidence in the next prompt ->
next worker action.
"""
import json
import sys

from agent.common.sgpt_workspace_mutation import run_ananta_worker_workspace_mutation
from agent.services.worker_workspace_service import WorkerWorkspaceService


class ScriptedLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def __call__(self, *, prompt, options, timeout, model, workdir):
        self.prompts.append(prompt)
        if not self.outputs:
            return 0, json.dumps({"kind": "final_answer", "answer": "exhausted"}), ""
        return 0, self.outputs.pop(0), ""


def _setup_workspace(tmp_path, *, materialized=("app.py",)):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    manifest = [
        {"workspace_path": rel, "allowed_operations": ["read", "write", "patch"]} for rel in materialized
    ]
    ananta_dir = workspace / ".ananta"
    ananta_dir.mkdir()
    (ananta_dir / "materialization-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return workspace


def _config(mode, **overrides):
    cfg = {
        "enabled": True,
        "resolved_mode": mode,
        "max_feedback_iterations": 4,
        "max_patch_attempts_per_file": 3,
        "max_invalid_outputs": 2,
        "max_diff_chars": 12000,
        "require_materialized_scope": True,
        "allowed_new_file_globs": [],
        "allowlisted_test_commands": [],
        "test_timeout_seconds": 30,
        "test_output_max_chars": 2000,
    }
    cfg.update(overrides)
    return cfg


def _run(workspace, llm, mode, **cfg_overrides):
    return run_ananta_worker_workspace_mutation(
        "Fix the add function",
        str(workspace),
        options=[],
        timeout=10,
        model=None,
        llm_runner=llm,
        config=_config(mode, **cfg_overrides),
    )


def _report(workspace):
    return json.loads((workspace / ".ananta" / "mutation-report.json").read_text(encoding="utf-8"))


# --- controlled_workspace (AWWPI-014) ----------------------------------------


def test_controlled_workspace_allows_materialized_change_and_creates_diff(tmp_path):
    workspace = _setup_workspace(tmp_path)
    fixed = "def add(a, b):\n    return a + b\n"
    llm = ScriptedLLM(
        [
            json.dumps({"kind": "workspace_write", "reason": "fix sign", "files": [{"path": "app.py", "content": fixed}]}),
            json.dumps({"kind": "final_answer", "answer": "fixed add()"}),
        ]
    )
    rc, out, err = _run(workspace, llm, "controlled_workspace")
    assert rc == 0
    assert out == "fixed add()"
    assert (workspace / "app.py").read_text(encoding="utf-8") == fixed
    # Feedback iteration: the second prompt must carry DiffResult + PolicyResult.
    assert "diff_result" in llm.prompts[1]
    assert "policy_result" in llm.prompts[1]
    assert "app.py" in llm.prompts[1]
    report = _report(workspace)
    assert report["outcome"] == "final_answer"
    assert report["final_policy_result"]["status"] == "ok"


def test_controlled_workspace_blocks_change_outside_manifest(tmp_path):
    workspace = _setup_workspace(tmp_path)
    llm = ScriptedLLM(
        [
            json.dumps(
                {
                    "kind": "workspace_write",
                    "files": [{"path": "sneaky.py", "content": "evil = True\n"}],
                }
            ),
            json.dumps({"kind": "final_answer", "answer": "done"}),
        ]
    )
    rc, out, err = _run(workspace, llm, "controlled_workspace")
    payload = json.loads(out)
    assert payload["kind"] == "final_answer_blocked"
    assert payload["status"] == "policy_blocked"
    report = _report(workspace)
    assert report["outcome"] == "policy_blocked"
    blocked_paths = [row["path"] for row in report["final_policy_result"]["blocked_changes"]]
    assert "sneaky.py" in blocked_paths


def test_read_only_mode_never_runs_mutation_loop(tmp_path):
    # The dispatch gate lives in sgpt.py: read_only never reaches the loop.
    # The contract is enforced by resolve_mutation_mode + the sgpt.py guard;
    # here we pin the baseline helper behavior for read_only.
    svc = WorkerWorkspaceService()
    meta = svc.refresh_mutation_baseline(workspace_dir=tmp_path, mutation_mode="read_only")
    assert meta["skipped"] == "read_only_mode"
    assert not (tmp_path / ".ananta" / "interactive-baseline").exists()


# --- strict_patch_request (AWWPI-015) ------------------------------------------


def test_strict_patch_request_applies_patch_via_hub(tmp_path):
    workspace = _setup_workspace(tmp_path)
    patch = "\n".join(
        [
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -1,2 +1,2 @@",
            " def add(a, b):",
            "-    return a - b",
            "+    return a + b",
        ]
    )
    llm = ScriptedLLM(
        [
            json.dumps(
                {
                    "kind": "patch_request",
                    "target_path": "app.py",
                    "variant": "unified_diff",
                    "unified_diff": patch,
                    "reason": "fix sign",
                }
            ),
            json.dumps({"kind": "final_answer", "answer": "patched"}),
        ]
    )
    rc, out, err = _run(workspace, llm, "strict_patch_request")
    assert out == "patched"
    assert (workspace / "app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    assert "patch_result" in llm.prompts[1]


def test_strict_mode_rejects_direct_workspace_write(tmp_path):
    workspace = _setup_workspace(tmp_path)
    llm = ScriptedLLM(
        [
            json.dumps({"kind": "workspace_write", "files": [{"path": "app.py", "content": "hacked\n"}]}),
            json.dumps({"kind": "final_answer", "answer": "done"}),
        ]
    )
    rc, out, err = _run(workspace, llm, "strict_patch_request")
    assert (workspace / "app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"
    assert "direct_write_not_allowed_in_strict_patch_request" in llm.prompts[1]


# --- feedback iteration (AWWPI-016) ---------------------------------------------


def test_failing_test_triggers_second_targeted_iteration(tmp_path):
    workspace = _setup_workspace(tmp_path)
    failing_cmd = f"{sys.executable} -c import_sys_exit_1"  # placeholder, replaced below
    failing_cmd = f"{sys.executable} -c exit(1)"
    fixed = "def add(a, b):\n    return a + b\n"
    llm = ScriptedLLM(
        [
            json.dumps({"kind": "tool_request", "tool_name": "test.run", "arguments": {"command": failing_cmd}}),
            json.dumps({"kind": "workspace_write", "reason": "fix after failing test", "files": [{"path": "app.py", "content": fixed}]}),
            json.dumps({"kind": "final_answer", "answer": "fixed after test feedback"}),
        ]
    )
    rc, out, err = _run(
        workspace, llm, "controlled_workspace", allowlisted_test_commands=[failing_cmd]
    )
    assert out == "fixed after test feedback"
    # Iteration 2 saw the failing TestResult as evidence.
    assert "test_result" in llm.prompts[1]
    assert '"rc": 1' in llm.prompts[1]
    report = _report(workspace)
    assert report["outcome"] == "final_answer"
    assert len(report["iterations"]) == 3


def test_non_allowlisted_test_command_is_policy_blocked(tmp_path):
    workspace = _setup_workspace(tmp_path)
    llm = ScriptedLLM(
        [
            json.dumps({"kind": "tool_request", "tool_name": "test.run", "arguments": {"command": "rm -rf /"}}),
            json.dumps({"kind": "final_answer", "answer": "ok"}),
        ]
    )
    rc, out, err = _run(workspace, llm, "controlled_workspace")
    assert "command_not_allowlisted" in llm.prompts[1]


def test_no_progress_detection_ends_loop(tmp_path):
    workspace = _setup_workspace(tmp_path)
    same_content = "def add(a, b):\n    return a - b  # noop rewrite\n"
    write = json.dumps({"kind": "workspace_write", "files": [{"path": "app.py", "content": same_content}]})
    llm = ScriptedLLM([write] * 6)
    rc, out, err = _run(workspace, llm, "controlled_workspace", max_feedback_iterations=6)
    payload = json.loads(out)
    assert payload["kind"] == "loop_aborted"
    assert payload["reason"] == "no_progress_detected"
    assert _report(workspace)["outcome"] == "no_progress_detected"


def test_invalid_output_limit_ends_loop(tmp_path):
    workspace = _setup_workspace(tmp_path)
    llm = ScriptedLLM(["garbage", "more garbage"])
    rc, out, err = _run(workspace, llm, "controlled_workspace")
    assert _report(workspace)["outcome"] == "invalid_output_limit_reached"


def test_max_patch_attempts_per_file_ends_loop(tmp_path):
    workspace = _setup_workspace(tmp_path)
    patch_request = json.dumps(
        {
            "kind": "patch_request",
            "target_path": "app.py",
            "variant": "unified_diff",
            "unified_diff": "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-does not exist\n+nope",
        }
    )
    llm = ScriptedLLM([patch_request] * 6)
    rc, out, err = _run(workspace, llm, "strict_patch_request", max_feedback_iterations=6, max_patch_attempts_per_file=2)
    payload = json.loads(out)
    assert payload["reason"] == "max_patch_attempts_per_file"


# --- artifact sync enforcement (AWWPI-017) ---------------------------------------


def test_policy_blocked_report_suppresses_success_artifacts(tmp_path):
    workspace = _setup_workspace(tmp_path)
    report = {
        "schema": "ananta_worker_mutation_report.v1",
        "outcome": "policy_blocked",
        "final_policy_result": {"status": "policy_violation", "blocked_changes": [{"path": "sneaky.py"}]},
    }
    (workspace / ".ananta" / "mutation-report.json").write_text(json.dumps(report), encoding="utf-8")
    filtered, note = WorkerWorkspaceService._mutation_sync_filter(
        workspace_dir=workspace, changed_rel_paths=["sneaky.py", "app.py"]
    )
    assert filtered == []
    assert note == "mutation_policy_blocked"


def test_clean_report_passes_changed_files_through(tmp_path):
    workspace = _setup_workspace(tmp_path)
    report = {
        "schema": "ananta_worker_mutation_report.v1",
        "outcome": "final_answer",
        "final_policy_result": {"status": "ok", "blocked_changes": []},
    }
    (workspace / ".ananta" / "mutation-report.json").write_text(json.dumps(report), encoding="utf-8")
    filtered, note = WorkerWorkspaceService._mutation_sync_filter(
        workspace_dir=workspace, changed_rel_paths=["app.py"]
    )
    assert filtered == ["app.py"]
    assert note is None
