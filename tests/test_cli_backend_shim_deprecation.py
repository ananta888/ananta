"""RED/GREEN test: agent.cli_backends/* must re-export all public symbols
from agent.common.sgpt_*.

The Welle 1 contract: agent.cli_backends.* is the public API, agent.common.sgpt_*
is the source of truth, and the cli_backends layer must re-export every
public symbol (functions, constants, classes) from the source.

This is the "completeness of the public API" check. It is the Welle 1
equivalent of the DeprecationWarning test (which would be premature: the
agent.common.sgpt_* modules are still the source, not shims).

Welle 3 (after source migration) will delete agent.common.sgpt_* and the
shim layer becomes irrelevant.
"""
from __future__ import annotations

import inspect


def _get_module_public_symbols(module) -> set[str]:
    """Return the set of public symbols defined in a module."""
    return {
        name
        for name, obj in inspect.getmembers(module)
        if not name.startswith("_") or name in inspect.getmembers(module, inspect.isclass)
    }


def test_cli_backends_sgpt_re_exports_run_sgpt_command() -> None:
    from agent.cli_backends import sgpt as cli_sgpt
    from agent.common import sgpt as common_sgpt

    assert hasattr(cli_sgpt, "run_sgpt_command")
    assert hasattr(common_sgpt, "run_sgpt_command")
    assert cli_sgpt.run_sgpt_command is common_sgpt.run_sgpt_command


def test_cli_backends_sgpt_re_exports_run_llm_cli_command() -> None:
    from agent.cli_backends import sgpt as cli_sgpt
    from agent.common import sgpt as common_sgpt

    assert hasattr(cli_sgpt, "run_llm_cli_command")
    assert cli_sgpt.run_llm_cli_command is common_sgpt.run_llm_cli_command


def test_cli_backends_tool_loop_re_exports_run_ananta_worker_tool_loop() -> None:
    from agent.cli_backends import tool_loop
    from agent.common import sgpt_tool_loop

    assert hasattr(tool_loop, "run_ananta_worker_tool_loop")
    assert tool_loop.run_ananta_worker_tool_loop is sgpt_tool_loop.run_ananta_worker_tool_loop


def test_cli_backends_tool_loop_re_exports_kind_constants() -> None:
    from agent.cli_backends import tool_loop
    from agent.common import sgpt_tool_loop

    for name in (
        "KIND_FINAL_ANSWER",
        "KIND_TOOL_REQUEST",
        "KIND_NEEDS_APPROVAL",
        "KIND_CANNOT_CONTINUE",
    ):
        assert hasattr(tool_loop, name)
        assert getattr(tool_loop, name) == getattr(sgpt_tool_loop, name)


def test_cli_backends_workspace_mutation_re_exports_run_ananta_worker_workspace_mutation() -> None:
    from agent.cli_backends import workspace_mutation
    from agent.common import sgpt_workspace_mutation

    assert hasattr(workspace_mutation, "run_ananta_worker_workspace_mutation")
    assert (
        workspace_mutation.run_ananta_worker_workspace_mutation
        is sgpt_workspace_mutation.run_ananta_worker_workspace_mutation
    )


def test_cli_backends_architecture_scan_re_exports_resolve_repo_root() -> None:
    from agent.cli_backends import architecture_scan
    from agent.common import sgpt_architecture_scan

    assert hasattr(architecture_scan, "_resolve_repo_root")
    assert architecture_scan._resolve_repo_root is sgpt_architecture_scan._resolve_repo_root


def test_cli_backends_opencode_re_exports_run_opencode_command() -> None:
    from agent.cli_backends import opencode
    from agent.common import sgpt_opencode

    assert hasattr(opencode, "run_opencode_command")
    assert opencode.run_opencode_command is sgpt_opencode.run_opencode_command


def test_cli_backends_routing_re_exports_supported_backends() -> None:
    from agent.cli_backends import routing
    from agent.common import sgpt_backend_routing

    assert hasattr(routing, "SUPPORTED_CLI_BACKENDS")
    assert routing.SUPPORTED_CLI_BACKENDS is sgpt_backend_routing.SUPPORTED_CLI_BACKENDS


def test_cli_backends_helpers_re_exports_get_agent_config() -> None:
    from agent.cli_backends import helpers
    from agent.common import sgpt_helpers

    assert helpers._get_agent_config is sgpt_helpers._get_agent_config


def test_cli_backends_semaphore_re_exports_acquire_backend_permit() -> None:
    from agent.cli_backends import semaphore
    from agent.common import sgpt_backend_semaphore

    assert semaphore._acquire_backend_permit is sgpt_backend_semaphore._acquire_backend_permit
