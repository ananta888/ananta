"""Tool and policy adapters for the workspace-mutation loop.

This module keeps service-owned helpers behind ``CliBackendContext`` so
the CLI backend package depends on its public DI boundary instead of
importing ``agent.services`` directly.
"""
from __future__ import annotations

from typing import Any

from agent.cli_backends.context import default_context


def execute_ananta_tool(**kwargs: Any) -> dict[str, Any]:
    return default_context.ananta_tool_executor(**kwargs)


def build_tool_result(**kwargs: Any) -> dict[str, Any]:
    return default_context.tool_result_builder(**kwargs)


def resolve_workspace_path(*args: Any, **kwargs: Any) -> Any:
    return default_context.workspace_path_resolver(*args, **kwargs)


def workspace_path_error_type() -> type[Exception]:
    return default_context.workspace_path_error_type


def extract_policy_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return default_context.generated_source_line_policy_helpers.extract_policy_config(cfg)


def generated_source_decision_blocked() -> str:
    return str(default_context.generated_source_line_policy_helpers.DECISION_BLOCKED)
