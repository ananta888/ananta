import json

from agent.cli_backends.workspace_mutation import run_ananta_worker_workspace_mutation
from agent.services.codecompass_context_planner_service import (
    SCHEMA_CONTEXT_BUNDLE,
    SCHEMA_LOCATION_REF,
    get_codecompass_context_planner,
)
from agent.services.tools import execute_ananta_tool
from agent.services.tools.codecompass_tools import codecompass_search


class ScriptedLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def __call__(self, *, prompt, options, timeout, model, workdir):
        self.prompts.append(prompt)
        if not self.outputs:
            return 0, json.dumps({"kind": "final_answer", "answer": "exhausted"}), ""
        return 0, self.outputs.pop(0), ""


class FakeRagService:
    def __init__(self, hits):
        self.hits = hits

    def retrieve(self, *, profile, query, limit):
        return list(self.hits)[:limit]


def _patch_rag(monkeypatch, hits):
    monkeypatch.setattr(
        "agent.services.rag_helper_index_service.get_rag_helper_index_service",
        lambda: FakeRagService(hits),
    )


def test_location_ref_contract_rejects_invalid_ranges():
    planner = get_codecompass_context_planner()
    assert planner.location_ref_from_hit({"path": "app.py", "line_start": 8, "line_end": 3}) is None
    ref = planner.location_ref_from_hit(
        {"path": "app.py", "line_start": 3, "line_end": 8, "symbol": "Service", "score": 0.8}
    )
    assert ref["schema"] == SCHEMA_LOCATION_REF
    assert ref["path"] == "app.py"
    assert ref["line_start"] == 3
    assert ref["line_end"] == 8
    assert ref["symbol"] == "Service"
    assert ref["location_id"].startswith("loc:")


def test_codecompass_search_adds_location_refs(monkeypatch, tmp_path):
    _patch_rag(
        monkeypatch,
        [
            {
                "path": "src/service.py",
                "content": "def retry(): pass",
                "metadata": {"start_line": 10, "end_line": 12, "symbol": "retry"},
                "score": 0.9,
            }
        ],
    )
    result = codecompass_search(
        workspace_dir=str(tmp_path),
        arguments={"query": "retry", "limit": 3},
        tool_call_id="tool_result:1",
    )
    refs = result["data"]["location_refs"]
    assert result["status"] == "ok"
    assert refs[0]["path"] == "src/service.py"
    assert refs[0]["line_start"] == 10
    assert refs[0]["line_end"] == 12


def test_plan_context_tool_returns_bounded_bundle(monkeypatch, tmp_path):
    _patch_rag(
        monkeypatch,
        [
            {
                "path": f"src/service_{idx}.py",
                "content": "x",
                "metadata": {"start_line": 1, "end_line": 500, "symbol": f"Service{idx}"},
                "score": 1.0 - idx / 100,
            }
            for idx in range(5)
        ],
    )
    result = execute_ananta_tool(
        tool_name="codecompass.plan_context",
        arguments={"query": "service", "max_ranges": 2, "max_lines_per_range": 10, "include_neighbors": False},
        workspace_dir=str(tmp_path),
        tool_call_id="tool_result:1",
    )
    bundle = result["data"]["context_bundle"]
    assert result["status"] == "ok"
    assert bundle["schema"] == SCHEMA_CONTEXT_BUNDLE
    assert len(bundle["location_refs"]) == 2
    assert bundle["location_refs"][0]["line_end"] == 10
    assert bundle["patch_targets"][0]["preferred_variant"] == "replace_range"
    assert bundle["excluded_refs"]


def test_worker_loop_materializes_context_bundle_ranges(monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (workspace / ".ananta").mkdir()
    (workspace / ".ananta" / "materialization-manifest.json").write_text(
        json.dumps([{"workspace_path": "app.py", "allowed_operations": ["read", "patch"]}]),
        encoding="utf-8",
    )
    _patch_rag(
        monkeypatch,
        [
            {
                "path": "app.py",
                "content": "def f():",
                "metadata": {"start_line": 1, "end_line": 2, "symbol": "f"},
                "score": 0.99,
            }
        ],
    )
    llm = ScriptedLLM(
        [
            json.dumps(
                {
                    "kind": "tool_request",
                    "tool_name": "codecompass.plan_context",
                    "arguments": {"query": "f", "max_ranges": 1, "include_neighbors": False},
                }
            ),
            json.dumps({"kind": "final_answer", "answer": "done"}),
        ]
    )
    run_ananta_worker_workspace_mutation(
        "Fix f",
        str(workspace),
        options=[],
        timeout=10,
        model=None,
        llm_runner=llm,
        config={
            "enabled": True,
            "resolved_mode": "strict_patch_request",
            "max_feedback_iterations": 3,
            "max_patch_attempts_per_file": 3,
            "max_invalid_outputs": 2,
            "max_diff_chars": 12000,
            "require_materialized_scope": True,
            "allowed_new_file_globs": [],
            "allowlisted_test_commands": [],
        },
    )
    second_prompt = llm.prompts[1]
    assert "materialized_range_results" in second_prompt
    assert "def f()" in second_prompt
