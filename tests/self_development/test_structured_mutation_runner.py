"""Structured-output runner: opt-in schema constraint for the mutation loop.

No live model is needed: the HTTP seam and the runner selection are exercised
with fakes, so these tests run headless like the rest of the suite.
"""
from __future__ import annotations

import io
import json
import urllib.error


def test_schema_covers_every_mutation_kind_and_parser_requirements():
    from agent.cli_backends.workspace_mutation.prompts import _ensure_kinds
    from agent.cli_backends.workspace_mutation.structured_runner import MUTATION_JSON_SCHEMA

    assert MUTATION_JSON_SCHEMA["required"] == ["kind"]
    assert MUTATION_JSON_SCHEMA.get("additionalProperties") is False

    branches = MUTATION_JSON_SCHEMA["anyOf"]
    kinds = set()
    for branch in branches:
        declared = branch["properties"]["kind"]["const"]
        kinds.add(declared)
        assert "kind" in branch["required"], f"{declared} must require the kind discriminator"
        assert branch.get("additionalProperties") is False, f"{declared} must not accept stray fields"

    assert kinds == _ensure_kinds()

    by_kind = {b["properties"]["kind"]["const"]: b for b in branches}
    # structural requirements of parse_mutation_output
    assert set(by_kind["workspace_write"]["properties"]["files"]["items"]["required"]) == {"path", "content"}
    assert "files" in by_kind["workspace_write"]["required"]
    assert "target_path" in by_kind["patch_request"]["required"]
    assert "tool_name" in by_kind["tool_request"]["required"]
    assert {"sources", "findings", "change_reasoning", "test_reasoning"} <= set(
        by_kind["code_analysis"]["required"]
    )
    assert "answer" in by_kind["final_answer"]["required"]


def test_runner_posts_json_schema_and_returns_a_parseable_answer(monkeypatch):
    from agent.cli_backends.workspace_mutation import structured_runner as mod
    from agent.cli_backends.workspace_mutation.prompts import parse_mutation_output

    answer = json.dumps(
        {"kind": "tool_request", "tool_name": "test.run", "arguments": {"command": "python -m pytest"}}
    )
    captured: dict = {}

    def fake_urlopen(request, timeout=None, *args, **kwargs):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        document = {"choices": [{"message": {"content": answer}}]}
        return io.BytesIO(json.dumps(document).encode("utf-8"))

    monkeypatch.setattr(mod, "_resolve_base_url", lambda: "http://llamacpp:18150/v1")
    monkeypatch.setattr(mod, "_resolve_model", lambda model: "ananta-code-27b")
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)

    rc, out, err = mod.run_structured_mutation_llm("prompt", options=[], timeout=30, model=None, workdir="/tmp")

    assert (rc, err) == (0, "")
    assert out == answer
    assert captured["url"] == "http://llamacpp:18150/v1/chat/completions"
    assert captured["timeout"] == 30
    payload = captured["payload"]
    assert payload["model"] == "ananta-code-27b"
    assert payload["temperature"] == mod.STRUCTURED_OUTPUT_TEMPERATURE
    assert "max_tokens" not in payload
    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == mod.MUTATION_SCHEMA_NAME
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == mod.MUTATION_JSON_SCHEMA
    assert parse_mutation_output(out) is not None


def test_runner_fails_fast_when_no_endpoint_or_model_resolves(monkeypatch):
    from agent.cli_backends.workspace_mutation import structured_runner as mod

    monkeypatch.setattr(mod, "_resolve_base_url", lambda: None)
    rc, out, err = mod.run_structured_mutation_llm("prompt")
    assert rc != 0 and out == "" and err == "structured_output_base_url_unresolved"

    monkeypatch.setattr(mod, "_resolve_base_url", lambda: "http://llamacpp:18150/v1")
    monkeypatch.setattr(mod, "_resolve_model", lambda model: None)
    rc, out, err = mod.run_structured_mutation_llm("prompt")
    assert rc != 0 and out == "" and err == "structured_output_model_unresolved"


def test_runner_reports_transport_errors_as_llm_failure(monkeypatch):
    from agent.cli_backends.workspace_mutation import structured_runner as mod

    def raise_http(request, timeout=None, *args, **kwargs):
        raise urllib.error.HTTPError(
            request.full_url, 500, "boom", hdrs=None, fp=io.BytesIO(b"server exploded")
        )

    monkeypatch.setattr(mod, "_resolve_base_url", lambda: "http://llamacpp:18150/v1")
    monkeypatch.setattr(mod, "_resolve_model", lambda model: "ananta-code-27b")
    monkeypatch.setattr(mod.urllib.request, "urlopen", raise_http)

    rc, out, err = mod.run_structured_mutation_llm("prompt", timeout=10)
    assert rc != 0
    assert out == ""
    assert err.startswith("structured_output_http_error:500")


def test_runner_rejects_an_empty_answer(monkeypatch):
    from agent.cli_backends.workspace_mutation import structured_runner as mod

    def fake_urlopen(request, timeout=None, *args, **kwargs):
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "   "}}]}).encode("utf-8"))

    monkeypatch.setattr(mod, "_resolve_base_url", lambda: "http://llamacpp:18150/v1")
    monkeypatch.setattr(mod, "_resolve_model", lambda model: "ananta-code-27b")
    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)

    rc, out, err = mod.run_structured_mutation_llm("prompt")
    assert rc != 0 and out == "" and err == "structured_output_empty_answer"


def test_runner_selection_stays_opt_in():
    from agent.cli_backends.sgpt import run_sgpt_command
    from agent.cli_backends.workspace_mutation import _orchestrator as orch
    from agent.cli_backends.workspace_mutation.structured_runner import run_structured_mutation_llm

    assert orch.resolve_mutation_llm_runner({}) is run_sgpt_command
    assert orch.resolve_mutation_llm_runner({"structured_output": False}) is run_sgpt_command
    assert orch.resolve_mutation_llm_runner({"structured_output": True}) is run_structured_mutation_llm

    def injected(prompt, options=None, timeout=60, model=None, workdir=None):
        return 0, "", ""

    assert orch.resolve_mutation_llm_runner({"structured_output": True}, injected) is injected


def test_env_override_is_additive(monkeypatch):
    from agent.config_defaults_env import apply_env_config_overrides

    monkeypatch.delenv("ANANTA_WORKSPACE_MUTATION_STRUCTURED_OUTPUT", raising=False)
    cfg: dict = {}
    apply_env_config_overrides(cfg)
    assert "structured_output" not in (cfg.get("ananta_worker_workspace_mutation") or {})

    monkeypatch.setenv("ANANTA_WORKSPACE_MUTATION_STRUCTURED_OUTPUT", "1")
    cfg = {}
    apply_env_config_overrides(cfg)
    assert cfg["ananta_worker_workspace_mutation"]["structured_output"] is True

    monkeypatch.setenv("ANANTA_WORKSPACE_MUTATION_STRUCTURED_OUTPUT", "0")
    cfg = {}
    apply_env_config_overrides(cfg)
    assert cfg["ananta_worker_workspace_mutation"]["structured_output"] is False


def test_model_resolution_skips_the_auto_alias(monkeypatch):
    from agent.cli_backends.workspace_mutation import structured_runner as mod

    monkeypatch.setattr(mod, "_get_agent_config", lambda: {"default_model": "auto"})
    assert mod._resolve_model(None) is None
    monkeypatch.setattr(mod, "_get_agent_config", lambda: {"default_model": "ananta-code-27b"})
    assert mod._resolve_model(None) == "ananta-code-27b"
    assert mod._resolve_model("explicit-model") == "explicit-model"
