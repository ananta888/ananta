"""Tests: agent.cli_backends.* public API surface.

These tests verify that the public API of the LLM-CLI backend subsystem
is complete and stable. Each module must export the symbols that
production code (and other tests) depend on.

The legacy "re-export" tests (comparing agent.cli_backends.X to
agent.common.sgpt_X) are gone because the legacy shim layer was
removed in Welle 4 of the SGDEC migration. agent.common.sgpt_*.py
no longer exists; agent.cli_backends.* is the source of truth.
"""
from __future__ import annotations


def test_cli_backends_sgpt_exposes_run_sgpt_command() -> None:
    from agent.cli_backends import sgpt

    assert hasattr(sgpt, "run_sgpt_command")
    assert hasattr(sgpt, "run_llm_cli_command")
    assert hasattr(sgpt, "_run_ananta_worker_iterative")
    assert hasattr(sgpt, "get_cli_backend_capabilities")
    assert hasattr(sgpt, "get_cli_backend_preflight")
    assert hasattr(sgpt, "resolve_codex_runtime_config")
    assert hasattr(sgpt, "resolve_opencode_runtime_config")


def test_cli_backends_tool_loop_exposes_run_ananta_worker_tool_loop() -> None:
    from agent.cli_backends import tool_loop

    assert hasattr(tool_loop, "run_ananta_worker_tool_loop")
    assert hasattr(tool_loop, "parse_worker_tool_output")


def test_cli_backends_tool_loop_exposes_kind_constants() -> None:
    from agent.cli_backends import tool_loop

    for name in (
        "KIND_FINAL_ANSWER",
        "KIND_TOOL_REQUEST",
        "KIND_NEEDS_APPROVAL",
        "KIND_CANNOT_CONTINUE",
    ):
        assert hasattr(tool_loop, name)
    assert tool_loop.KIND_FINAL_ANSWER == "final_answer"
    assert tool_loop.KIND_TOOL_REQUEST == "tool_request"


def test_cli_backends_workspace_mutation_exposes_run_ananta_worker_workspace_mutation() -> None:
    from agent.cli_backends import workspace_mutation

    assert hasattr(workspace_mutation, "run_ananta_worker_workspace_mutation")
    assert hasattr(workspace_mutation, "parse_mutation_output")
    assert hasattr(workspace_mutation, "build_iteration_prompt")
    assert hasattr(workspace_mutation, "build_mode_instructions")
    assert hasattr(workspace_mutation, "evidence_signature")
    assert hasattr(workspace_mutation, "changes_signature")


def test_cli_backends_architecture_scan_exposes_resolve_repo_root() -> None:
    from agent.cli_backends import architecture_scan

    assert hasattr(architecture_scan, "_resolve_repo_root")
    assert hasattr(architecture_scan, "_load_source_file_batches")


def test_cli_backends_opencode_exposes_run_opencode_command() -> None:
    from agent.cli_backends import opencode

    assert hasattr(opencode, "run_opencode_command")
    assert hasattr(opencode, "run_codex_command")
    assert hasattr(opencode, "run_aider_command")
    assert hasattr(opencode, "run_mistral_code_command")


def test_cli_backends_routing_exposes_supported_backends() -> None:
    from agent.cli_backends import routing

    assert hasattr(routing, "SUPPORTED_CLI_BACKENDS")
    assert hasattr(routing, "CLI_BACKEND_CAPABILITIES")
    assert hasattr(routing, "get_cli_backend_capabilities")


def test_cli_backends_helpers_exposes_get_agent_config() -> None:
    from agent.cli_backends import helpers

    assert hasattr(helpers, "_get_agent_config")
    assert hasattr(helpers, "_get_runtime_provider_urls")
    assert hasattr(helpers, "_normalize_openai_base_url")
    assert hasattr(helpers, "_resolve_openai_compatible_base_url")


def test_cli_backends_semaphore_exposes_acquire_backend_permit() -> None:
    from agent.cli_backends import semaphore

    assert hasattr(semaphore, "_acquire_backend_permit")
    assert hasattr(semaphore, "_resolve_backend_parallel_limit")
    assert hasattr(semaphore, "_get_backend_semaphore")


def test_agent_common_sgpt_namespace_removed() -> None:
    """The legacy agent.common.sgpt_*.py shim layer is gone.

    This test enforces the Welle-4 final state: agent.cli_backends.*
    is the source of truth, the legacy shim layer is deleted.
    """
    import importlib

    legacy_modules = [
        "agent.common.sgpt",
        "agent.common.sgpt_helpers",
        "agent.common.sgpt_backend_semaphore",
        "agent.common.sgpt_backend_routing",
        "agent.common.sgpt_tool_loop",
        "agent.common.sgpt_opencode",
        "agent.common.sgpt_architecture_scan",
        "agent.common.sgpt_workspace_mutation",
    ]
    for mod_name in legacy_modules:
        try:
            importlib.import_module(mod_name)
            raise AssertionError(
                f"{mod_name} still exists; it should have been deleted in Welle 4"
            )
        except ModuleNotFoundError:
            pass  # expected
