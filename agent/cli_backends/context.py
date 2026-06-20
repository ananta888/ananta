"""CliBackendContext — DI-box for LLM-CLI backend service handles.

Skeleton for Welle 1. Properties delegate to the real service getters.
In Welle 2 the cli_backends source modules will be wired to call
``default_context.<service>`` instead of importing ``agent.services.*``
directly — that gives tests a single monkeypatch target.
"""
from __future__ import annotations

from typing import Any


class CliBackendContext:
    """DI-box for LLM-CLI backend service handles."""

    @property
    def approval_request_service(self) -> Any:
        from agent.services.approval_request_service import get_approval_request_service

        return get_approval_request_service()

    @property
    def ananta_tool_policy_service(self) -> Any:
        from agent.services.ananta_tool_policy_service import get_ananta_tool_policy_service

        return get_ananta_tool_policy_service()

    @property
    def ananta_tool_registry_service(self) -> Any:
        from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

        return get_ananta_tool_registry_service()

    @property
    def ananta_workspace_mutation_policy_service(self) -> Any:
        from agent.services.ananta_workspace_mutation_policy import (
            get_ananta_workspace_mutation_policy_service,
        )

        return get_ananta_workspace_mutation_policy_service()

    @property
    def generated_source_line_policy_service(self) -> Any:
        from agent.services.generated_source_line_policy_service import (
            get_generated_source_line_policy_service,
        )

        return get_generated_source_line_policy_service()

    @property
    def worker_workspace_service(self) -> Any:
        from agent.services.worker_workspace_service import get_worker_workspace_service

        return get_worker_workspace_service()

    @property
    def model_invocation_service(self) -> Any:
        from agent.services.model_invocation_service import ModelInvocationService

        return ModelInvocationService

    @property
    def opencode_runtime_service(self) -> Any:
        from agent.services.opencode_runtime_service import get_opencode_runtime_service

        return get_opencode_runtime_service()

    @property
    def live_terminal_session_service(self) -> Any:
        from agent.services.live_terminal_session_service import (
            get_live_terminal_session_service,
        )

        return get_live_terminal_session_service()

    @property
    def architecture_analysis_planner(self) -> Any:
        from agent.services.architecture_analysis_planner_service import (
            get_architecture_analysis_planner,
        )

        return get_architecture_analysis_planner()


default_context = CliBackendContext()
