"""CliBackendContext — DI-box for LLM-CLI backend service handles.

DI pattern: each service is a property with a setter. The default getter
delegates to the corresponding ``agent.services.get_*_service()`` call.
Tests can override the value by setting the attribute:

    ctx = default_context
    ctx.approval_request_service = fake_service  # setter accepts override

Or via monkeypatch:
    monkeypatch.setattr(default_context, "approval_request_service", fake)

The property has a setter so that instance attribute assignment works
(without a setter, Python raises AttributeError).
"""
from __future__ import annotations

from typing import Any, Callable


class _ServiceProperty:
    """Descriptor that exposes a lazy service getter with settable override.

    Behaves like @property: getter on read, setter on assignment.
    The default getter is a callable that takes no args and returns the
    service handle.

    IMPORTANT: the override is stored on the instance's __dict__ (not on
    the descriptor itself). This keeps the override local to a single
    CliBackendContext instance — a test that sets an override on a fresh
    instance does not leak into the module-level ``default_context``.
    """

    def __init__(self, getter: Callable[[], Any]) -> None:
        # Map: id(instance) -> overridden value, or _UNSET_SENTINEL.
        # This keeps the override scoped to one instance and survives GC
        # of the instance (the descriptor itself is class-level).
        self._getter = getter
        self._overrides: dict[int, Any] = {}

    def __get__(self, instance: Any, owner: Any) -> Any:
        if instance is None:
            return self
        key = id(instance)
        if key in self._overrides:
            return self._overrides[key]
        return self._getter()

    def __set__(self, instance: Any, value: Any) -> None:
        self._overrides[id(instance)] = value

    def __delete__(self, instance: Any) -> None:
        self._overrides.pop(id(instance), None)


_UNSET = object()


class CliBackendContext:
    """DI-box for LLM-CLI backend service handles.

    All service handles are public attributes backed by _ServiceProperty
    descriptors. Tests can override them by direct assignment, and the
    module-level ``default_context`` singleton is the shared instance.
    """

    approval_request_service = _ServiceProperty(
        lambda: __import__("agent.services.approval_request_service", fromlist=["get_approval_request_service"]).get_approval_request_service()
    )
    ananta_tool_policy_service = _ServiceProperty(
        lambda: __import__("agent.services.ananta_tool_policy_service", fromlist=["get_ananta_tool_policy_service"]).get_ananta_tool_policy_service()
    )
    ananta_tool_registry_service = _ServiceProperty(
        lambda: __import__("agent.services.ananta_tool_registry_service", fromlist=["get_ananta_tool_registry_service"]).get_ananta_tool_registry_service()
    )
    ananta_workspace_mutation_policy_service = _ServiceProperty(
        lambda: __import__("agent.services.ananta_workspace_mutation_policy", fromlist=["get_ananta_workspace_mutation_policy_service"]).get_ananta_workspace_mutation_policy_service()
    )
    generated_source_line_policy_service = _ServiceProperty(
        lambda: __import__("agent.services.generated_source_line_policy_service", fromlist=["get_generated_source_line_policy_service"]).get_generated_source_line_policy_service()
    )
    worker_workspace_service = _ServiceProperty(
        lambda: __import__("agent.services.worker_workspace_service", fromlist=["get_worker_workspace_service"]).get_worker_workspace_service()
    )
    model_invocation_service = _ServiceProperty(
        lambda: __import__("agent.services.model_invocation_service", fromlist=["ModelInvocationService"]).ModelInvocationService
    )
    opencode_runtime_service = _ServiceProperty(
        lambda: __import__("agent.services.opencode_runtime_service", fromlist=["get_opencode_runtime_service"]).get_opencode_runtime_service()
    )
    live_terminal_session_service = _ServiceProperty(
        lambda: __import__("agent.services.live_terminal_session_service", fromlist=["get_live_terminal_session_service"]).get_live_terminal_session_service()
    )
    architecture_analysis_planner = _ServiceProperty(
        lambda: __import__("agent.services.architecture_analysis_planner_service", fromlist=["get_architecture_analysis_planner"]).get_architecture_analysis_planner()
    )
    ananta_tool_executor = _ServiceProperty(
        lambda: __import__("agent.services.tools", fromlist=["execute_ananta_tool"]).execute_ananta_tool
    )
    tool_result_builder = _ServiceProperty(
        lambda: __import__("agent.services.tools._evidence", fromlist=["build_tool_result"]).build_tool_result
    )
    workspace_path_resolver = _ServiceProperty(
        lambda: __import__("agent.services.tools.repo_tools", fromlist=["resolve_workspace_path"]).resolve_workspace_path
    )
    workspace_path_error_type = _ServiceProperty(
        lambda: __import__("agent.services.tools.repo_tools", fromlist=["WorkspacePathError"]).WorkspacePathError
    )
    generated_source_line_policy_helpers = _ServiceProperty(
        lambda: __import__(
            "agent.services.generated_source_line_policy_service",
            fromlist=["DECISION_BLOCKED", "extract_policy_config"],
        )
    )


# Module-level singleton — tests monkeypatch the class or the instance.
default_context = CliBackendContext()
