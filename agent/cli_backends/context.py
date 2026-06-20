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
    """

    def __init__(self, getter: Callable[[], Any]) -> None:
        self._getter = getter
        self._value: Any = _UNSET

    def __get__(self, instance: Any, owner: Any) -> Any:
        if instance is None:
            return self
        if self._value is _UNSET:
            return self._getter()
        return self._value

    def __set__(self, instance: Any, value: Any) -> None:
        self._value = value

    def __delete__(self, instance: Any) -> None:
        self._value = _UNSET


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


# Module-level singleton — tests monkeypatch the class or the instance.
default_context = CliBackendContext()
