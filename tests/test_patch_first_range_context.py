import hashlib
import json

from agent.common.sgpt_workspace_mutation import run_ananta_worker_workspace_mutation
from agent.services.tools.repo_tools import repo_grep, repo_read_file_range
from agent.services.tools.workspace_mutation_tools import repo_apply_patch, repo_write_file


class ScriptedLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def __call__(self, *, prompt, options, timeout, model, workdir):
        self.prompts.append(prompt)
        if not self.outputs:
            return 0, json.dumps({"kind": "final_answer", "answer": "exhausted"}), ""
        return 0, self.outputs.pop(0), ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_read_file_range_returns_hash_metadata_without_full_prompt_payload(tmp_path):
    target = tmp_path / "large.py"
    target.write_text("\n".join(f"line {idx}" for idx in range(1, 800)) + "\n", encoding="utf-8")
    result = repo_read_file_range(
        workspace_dir=str(tmp_path),
        arguments={"path": "large.py", "line_start": 100, "line_end": 110},
        tool_call_id="tool_result:1",
    )
    assert result["status"] == "ok"
    assert result["data"]["total_lines"] == 799
    assert result["data"]["file_sha256"]
    assert result["data"]["range_sha256"]
    excerpt = result["evidence"][0]["excerpt"]
    assert "line 100" in excerpt
    assert "line 99" not in excerpt
    assert "line 111" not in excerpt


def test_grep_context_window_is_bounded(tmp_path):
    (tmp_path / "app.py").write_text("before\nneedle\nnext\nlast\n", encoding="utf-8")
    result = repo_grep(
        workspace_dir=str(tmp_path),
        arguments={"pattern": "needle", "context_before": 1, "context_after": 1},
        tool_call_id="tool_result:1",
    )
    assert result["status"] == "ok"
    assert result["evidence"][0]["line_start"] == 1
    assert result["evidence"][0]["line_end"] == 3
    assert result["evidence"][0]["excerpt"] == "before\nneedle\nnext"


def test_replace_range_patch_variant_is_hash_bound(tmp_path):
    target = tmp_path / "app.py"
    original = "def add(a, b):\n    return a - b\n"
    target.write_text(original, encoding="utf-8")
    result = repo_apply_patch(
        workspace_dir=str(tmp_path),
        arguments={
            "target_path": "app.py",
            "variant": "replace_range",
            "line_start": 2,
            "line_end": 2,
            "replacement": "    return a + b",
            "expected_old_hash": _sha(original),
        },
        tool_call_id="patch_result:1",
    )
    assert result["status"] == "ok"
    assert result["data"]["variant"] == "replace_range"
    assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_replace_existing_large_file_is_blocked(tmp_path):
    target = tmp_path / "big.py"
    original = "x" * 70_000
    target.write_text(original, encoding="utf-8")
    result = repo_write_file(
        workspace_dir=str(tmp_path),
        arguments={
            "path": "big.py",
            "mode": "replace_existing",
            "content": "small\n",
            "expected_old_hash": _sha(original),
        },
        tool_call_id="tool_result:1",
    )
    assert result["status"] == "rejected"
    assert result["data"]["rejected_reason"] == "replace_existing_file_too_large"


def test_strict_prompt_prefers_codecompass_range_patch_diff_test_flow(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (workspace / ".ananta").mkdir()
    (workspace / ".ananta" / "materialization-manifest.json").write_text(
        json.dumps([{"workspace_path": "app.py", "allowed_operations": ["read", "patch"]}]),
        encoding="utf-8",
    )
    llm = ScriptedLLM([json.dumps({"kind": "final_answer", "answer": "done"})])
    run_ananta_worker_workspace_mutation(
        "Fix app.py",
        str(workspace),
        options=[],
        timeout=10,
        model=None,
        llm_runner=llm,
        config={
            "enabled": True,
            "resolved_mode": "strict_patch_request",
            "max_feedback_iterations": 1,
            "max_patch_attempts_per_file": 3,
            "max_invalid_outputs": 2,
            "max_diff_chars": 12000,
            "require_materialized_scope": True,
            "allowed_new_file_globs": [],
            "allowlisted_test_commands": [],
        },
    )
    prompt = llm.prompts[0]
    assert "codecompass.plan_context -> repo.read_file_range -> patch_request -> workspace.diff -> test.run" in prompt
    assert "kompletter neuer Inhalt" not in prompt
