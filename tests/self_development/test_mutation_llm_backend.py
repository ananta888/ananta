"""The mutation loop's brain selection (L6 variant A).

The loop's structure is constant; which CLI answers its protocol turns is
configuration (``ANANTA_WORKSPACE_MUTATION_LLM_BACKEND``). These tests pin the
runner precedence (injected > brain > structured > CLI), the opencode adapter's
``(rc, out, err)`` contract and the env plumbing with its bounded validation.
"""

from __future__ import annotations

import pytest

from agent.cli_backends.workspace_mutation._orchestrator import resolve_mutation_llm_runner
from agent.cli_backends.workspace_mutation.opencode_runner import run_opencode_mutation_llm
from agent.cli_backends.workspace_mutation.structured_runner import run_structured_mutation_llm
from agent.config_defaults_env import apply_env_config_overrides


def _injected(prompt: str, options=None, timeout=60, model=None, workdir=None):
    return 0, "injected", ""


def test_injected_runner_wins():
    assert resolve_mutation_llm_runner({"llm_backend": "opencode"}, llm_runner=_injected) is _injected


def test_opencode_brain_is_selected_from_cfg():
    runner = resolve_mutation_llm_runner({"llm_backend": "opencode"})
    assert runner is run_opencode_mutation_llm


def test_brain_outranks_structured_output():
    runner = resolve_mutation_llm_runner({"llm_backend": "opencode", "structured_output": True})
    assert runner is run_opencode_mutation_llm


def test_structured_output_still_applies_without_a_brain():
    runner = resolve_mutation_llm_runner({"structured_output": True})
    assert runner is run_structured_mutation_llm


def test_default_stays_the_cli_runner():
    from agent.cli_backends.sgpt import run_sgpt_command

    assert resolve_mutation_llm_runner({}) is run_sgpt_command


def test_opencode_adapter_returns_the_three_tuple_and_forwards(monkeypatch):
    captured = {}

    def fake_dispatch(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return 0, "protocol answer", "ignored fourth element", "opencode"

    monkeypatch.setattr("agent.cli_backends.sgpt.run_llm_cli_command", fake_dispatch)
    rc, out, err = run_opencode_mutation_llm(
        "PROMPT",
        options=["--foo"],
        timeout=42,
        model="m",
        workdir="/ws",
    )
    assert (rc, out, err) == (0, "protocol answer", "ignored fourth element")
    assert captured["backend"] == "opencode"
    # the loop brain answers protocol turns without its own tools or native MCP —
    # they would bypass the loop's CodeCompass steps and stall the turn timeout
    assert captured["routing_policy"] == {"opencode_tool_mode": "protocol"}
    assert captured["prompt"] == "PROMPT"
    assert captured["options"] == ["--foo"]
    assert captured["timeout"] == 42
    assert captured["model"] == "m"
    assert captured["workdir"] == "/ws"


def test_env_plumbing_writes_the_brain_into_the_mutation_section(monkeypatch):
    monkeypatch.setenv("ANANTA_WORKSPACE_MUTATION_LLM_BACKEND", "opencode")
    monkeypatch.setenv("ANANTA_WORKSPACE_MUTATION_STRUCTURED_OUTPUT", "1")
    cfg: dict = {}
    apply_env_config_overrides(cfg)
    section = cfg["ananta_worker_workspace_mutation"]
    assert section["llm_backend"] == "opencode"
    assert section["structured_output"] is True


def test_env_plumbing_stays_additive(monkeypatch):
    monkeypatch.delenv("ANANTA_WORKSPACE_MUTATION_LLM_BACKEND", raising=False)
    monkeypatch.delenv("ANANTA_WORKSPACE_MUTATION_STRUCTURED_OUTPUT", raising=False)
    cfg: dict = {"ananta_worker_workspace_mutation": {"enabled": True}}
    apply_env_config_overrides(cfg)
    assert cfg["ananta_worker_workspace_mutation"] == {"enabled": True}


def test_env_rejects_an_unknown_brain_bounded(monkeypatch):
    monkeypatch.setenv("ANANTA_WORKSPACE_MUTATION_LLM_BACKEND", "mystery-cli")
    with pytest.raises(ValueError, match="ANANTA_WORKSPACE_MUTATION_LLM_BACKEND"):
        apply_env_config_overrides({})
