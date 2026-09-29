"""RED test: agent.cli_backends namespace skeleton must exist and be importable.

This is the Source-of-Truth location for the LLM-CLI backend subsystem.
Per SGDEC plan, all sgpt_* modules move here from agent.common.
"""
from __future__ import annotations


def test_cli_backends_package_importable() -> None:
    """The agent.cli_backends package must be importable."""
    import agent.cli_backends

    assert agent.cli_backends is not None


def test_cli_backends_has_context_module() -> None:
    """The CliBackendContext DI class must be importable from agent.cli_backends.context."""
    from agent.cli_backends import context

    assert context is not None
    assert hasattr(context, "CliBackendContext"), "CliBackendContext class must exist"


def test_cli_backends_helpers_module_importable() -> None:
    """sgpt_helpers moves to agent.cli_backends.helpers — must be importable."""
    from agent.cli_backends import helpers

    # These are the public symbols from sgpt_helpers.py
    assert hasattr(helpers, "_get_agent_config")
    assert hasattr(helpers, "_get_runtime_default_provider")
    assert hasattr(helpers, "_get_runtime_provider_urls")
    assert hasattr(helpers, "_resolve_profile_api_key")
    assert hasattr(helpers, "_is_probably_local_base_url")
    assert hasattr(helpers, "_normalize_openai_base_url")
    assert hasattr(helpers, "_normalize_ollama_openai_base_url")
    assert hasattr(helpers, "_resolve_openai_compatible_base_url")
    assert hasattr(helpers, "_classify_runtime_target")


def test_cli_backends_semaphore_module_importable() -> None:
    """sgpt_backend_semaphore moves to agent.cli_backends.semaphore — must be importable."""
    from agent.cli_backends import semaphore

    assert hasattr(semaphore, "_BACKEND_SEMAPHORES")
    assert hasattr(semaphore, "_acquire_backend_permit")
    assert hasattr(semaphore, "_get_backend_semaphore")


def test_cli_backends_routing_module_importable() -> None:
    """sgpt_backend_routing moves to agent.cli_backends.routing — must be importable."""
    from agent.cli_backends import routing

    assert hasattr(routing, "SUPPORTED_CLI_BACKENDS")
    assert hasattr(routing, "CLI_BACKEND_INSTALL_HINTS")
    assert hasattr(routing, "CLI_BACKEND_VERIFY_COMMANDS")
    assert hasattr(routing, "CLI_BACKEND_CAPABILITIES")
    assert hasattr(routing, "get_cli_backend_capabilities")
    assert hasattr(routing, "get_cli_backend_preflight")
    assert hasattr(routing, "get_cli_backend_runtime_status")
    assert hasattr(routing, "get_research_backend_preflight")
    assert hasattr(routing, "normalize_backend_flags")


def test_cli_backends_tool_loop_module_importable() -> None:
    """sgpt_tool_loop moves to agent.cli_backends.tool_loop — must be importable."""
    from agent.cli_backends import tool_loop

    assert hasattr(tool_loop, "get_tool_loop_config")
    assert hasattr(tool_loop, "parse_worker_tool_output")
    assert hasattr(tool_loop, "build_tool_loop_instructions")
    assert hasattr(tool_loop, "build_tool_loop_prompt")
    assert hasattr(tool_loop, "register_pending_approval_request")
    assert hasattr(tool_loop, "run_ananta_worker_tool_loop")


def test_cli_backends_opencode_module_importable() -> None:
    """sgpt_opencode moves to agent.cli_backends.opencode — must be importable."""
    from agent.cli_backends import opencode

    assert hasattr(opencode, "resolve_opencode_runtime_config")
    assert hasattr(opencode, "resolve_codex_runtime_config")
    assert hasattr(opencode, "run_opencode_command")
    assert hasattr(opencode, "run_codex_command")
    assert hasattr(opencode, "run_aider_command")
    assert hasattr(opencode, "run_mistral_code_command")


def test_cli_backends_architecture_scan_module_importable() -> None:
    """sgpt_architecture_scan moves to agent.cli_backends.architecture_scan — must be importable."""
    from agent.cli_backends import architecture_scan

    assert hasattr(architecture_scan, "_resolve_repo_root")
    assert hasattr(architecture_scan, "_run_architecture_full_scan")
    assert hasattr(architecture_scan, "_build_iteration_prompt")


def test_cli_backends_workspace_mutation_module_importable() -> None:
    """sgpt_workspace_mutation moves to agent.cli_backends.workspace_mutation — must be importable."""
    from agent.cli_backends import workspace_mutation

    assert hasattr(workspace_mutation, "run_ananta_worker_workspace_mutation")
    assert hasattr(workspace_mutation, "get_workspace_mutation_config")
    assert hasattr(workspace_mutation, "parse_mutation_output")
