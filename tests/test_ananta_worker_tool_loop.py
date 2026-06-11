"""AWTCL-017: unit tests for the ananta-worker tool calling loop."""
import json

import pytest

from agent.common.sgpt_tool_loop import (
    KIND_FINAL_ANSWER,
    KIND_TOOL_REQUEST,
    parse_worker_tool_output,
    run_ananta_worker_tool_loop,
)


class ScriptedLLM:
    """Deterministic llm_runner stand-in: returns scripted outputs in order."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def __call__(self, *, prompt, options, timeout, model, workdir):
        self.prompts.append(prompt)
        if not self.outputs:
            return 0, json.dumps({"kind": "final_answer", "answer": "exhausted"}), ""
        return 0, self.outputs.pop(0), ""


def _loop_config(**overrides):
    cfg = {
        "enabled": True,
        "max_iterations": 6,
        "max_tool_calls": 12,
        "max_tool_result_chars": 8000,
        "max_invalid_outputs": 2,
        "allowed_tools": [
            "repo.list_files",
            "repo.read_file_range",
            "repo.grep",
            "codecompass.search",
        ],
    }
    cfg.update(overrides)
    return cfg


# --- parser (AWTCL-009) -----------------------------------------------------


def test_parser_accepts_raw_json_tool_request():
    payload = json.dumps(
        {"kind": "tool_request", "tool_name": "repo.grep", "arguments": {"pattern": "x"}}
    )
    message = parse_worker_tool_output(payload)
    assert message is not None
    assert message["kind"] == KIND_TOOL_REQUEST
    assert message["tool_name"] == "repo.grep"


def test_parser_accepts_fenced_json_final_answer():
    text = "Here you go:\n```json\n{\"kind\": \"final_answer\", \"answer\": \"done\"}\n```\nthanks"
    message = parse_worker_tool_output(text)
    assert message is not None
    assert message["kind"] == KIND_FINAL_ANSWER
    assert message["answer"] == "done"


@pytest.mark.parametrize(
    "text",
    [
        "plain prose without json",
        '{"kind": "tool_request"}',  # missing tool_name
        '{"kind": "unknown_kind", "tool_name": "repo.grep"}',
        '{"kind": "tool_request", "tool_name": "repo.grep", "arguments": "not-a-dict"}',
        "",
    ],
)
def test_parser_rejects_invalid_outputs(text):
    assert parse_worker_tool_output(text) is None


# --- loop (AWTCL-010) -------------------------------------------------------


def test_loop_grep_then_final_answer(tmp_path):
    (tmp_path / "module.py").write_text("class ToolRoutingService:\n    pass\n", encoding="utf-8")
    llm = ScriptedLLM(
        [
            json.dumps(
                {
                    "kind": "tool_request",
                    "tool_name": "repo.grep",
                    "reason": "find usage",
                    "arguments": {"pattern": "ToolRoutingService", "limit": 10},
                }
            ),
            json.dumps({"kind": "final_answer", "answer": "found it", "evidence_refs": ["tool_result:1"]}),
        ]
    )
    rc, out, err = run_ananta_worker_tool_loop(
        "Find ToolRoutingService", str(tmp_path), options=[], timeout=10, model=None,
        llm_runner=llm, config=_loop_config(),
    )
    assert rc == 0
    assert out == "found it"
    # The grep evidence must be embedded in the second prompt.
    assert "tool_result:1" in llm.prompts[1]
    assert "ToolRoutingService" in llm.prompts[1]
    report = json.loads((tmp_path / ".ananta" / "tool-loop-report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "final_answer"
    assert report["tool_call_count"] == 1


def test_loop_unknown_tool_is_policy_blocked(tmp_path):
    llm = ScriptedLLM(
        [
            json.dumps({"kind": "tool_request", "tool_name": "made.up_tool", "arguments": {}}),
            json.dumps({"kind": "final_answer", "answer": "ok"}),
        ]
    )
    rc, out, err = run_ananta_worker_tool_loop(
        "task", str(tmp_path), options=[], timeout=10, model=None, llm_runner=llm, config=_loop_config(),
    )
    assert rc == 0
    assert out == "ok"
    assert "policy_blocked" in llm.prompts[1]
    assert "unknown_tool" in llm.prompts[1]


def test_loop_max_iterations_prevents_endless_loop(tmp_path):
    request = json.dumps({"kind": "tool_request", "tool_name": "repo.list_files", "arguments": {}})
    llm = ScriptedLLM([request] * 50)
    rc, out, err = run_ananta_worker_tool_loop(
        "task", str(tmp_path), options=[], timeout=10, model=None, llm_runner=llm,
        config=_loop_config(max_iterations=3, max_tool_calls=50),
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["kind"] == "loop_aborted"
    assert payload["reason"] == "max_iterations_reached"
    assert len(llm.prompts) == 3


def test_loop_max_tool_calls_aborts(tmp_path):
    request = json.dumps({"kind": "tool_request", "tool_name": "repo.list_files", "arguments": {}})
    llm = ScriptedLLM([request] * 50)
    rc, out, err = run_ananta_worker_tool_loop(
        "task", str(tmp_path), options=[], timeout=10, model=None, llm_runner=llm,
        config=_loop_config(max_iterations=10, max_tool_calls=2),
    )
    payload = json.loads(out)
    assert payload["reason"] == "max_tool_calls_reached"


def test_loop_invalid_json_falls_back_to_text(tmp_path):
    llm = ScriptedLLM(["not json at all", "still not json"])
    rc, out, err = run_ananta_worker_tool_loop(
        "task", str(tmp_path), options=[], timeout=10, model=None, llm_runner=llm,
        config=_loop_config(max_invalid_outputs=2),
    )
    assert rc == 0
    assert out == "still not json"
    report = json.loads((tmp_path / ".ananta" / "tool-loop-report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "invalid_output_fallback"


def test_loop_needs_approval_stops_loop(tmp_path):
    llm = ScriptedLLM([json.dumps({"kind": "needs_approval", "reason": "wants git push"})])
    rc, out, err = run_ananta_worker_tool_loop(
        "task", str(tmp_path), options=[], timeout=10, model=None, llm_runner=llm, config=_loop_config(),
    )
    payload = json.loads(out)
    assert payload["kind"] == "needs_approval"
    assert payload["reason"] == "wants git push"
