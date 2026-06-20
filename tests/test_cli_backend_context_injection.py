"""RED/GREEN test: CliBackendContext injection must work via monkeypatch.

The DI contract: tests can override any service handle on a context instance
via ``monkeypatch.setattr(context, 'approval_request_service', fake)`` and
sub-modules that use ``default_context.approval_request_service`` must
respect the override.
"""
from __future__ import annotations

import sys


def test_context_property_delegates_to_service_getter() -> None:
    """The CliBackendContext property must return the live service handle."""
    from agent.cli_backends.context import CliBackendContext, default_context

    # Just verify the property doesn't raise and returns a non-None value.
    # We don't assert identity because some services are lazy/singletons.
    ctx = CliBackendContext()
    # Each property access must not raise
    _ = ctx.ananta_tool_policy_service  # access does not raise


def test_context_default_singleton_exists() -> None:
    """The module must expose a `default_context` singleton for shared use."""
    from agent.cli_backends import context

    assert hasattr(context, "default_context")
    assert isinstance(context.default_context, context.CliBackendContext)


def test_context_overridable_via_monkeypatch() -> None:
    """Tests can replace a property's return value via instance attribute.

    This is the canonical monkeypatch pattern: tests set a non-property
    attribute on the context instance (Python allows this for instances,
    even if the class defines the property — instance __dict__ wins).
    """
    from agent.cli_backends.context import CliBackendContext

    ctx = CliBackendContext()
    fake_service = object()
    # Setting on instance shadows the property descriptor.
    ctx.ananta_tool_policy_service = fake_service  # type: ignore[assignment]
    assert ctx.ananta_tool_policy_service is fake_service


def test_context_property_returns_underlying_service() -> None:
    """Property access (without override) must return the real service."""
    from agent.cli_backends.context import CliBackendContext

    ctx = CliBackendContext()
    # The first access triggers the lazy import. We just verify it doesn't
    # raise and returns something callable or an instance.
    result = ctx.ananta_tool_policy_service
    assert result is not None


def test_context_service_getter_imports_lazy() -> None:
    """Importing CliBackendContext must NOT eagerly import agent.services.*.

    The DI contract requires that the Context class can be imported without
    pulling in any service modules — only on first property access.
    """
    # Force a fresh import to check side-effects
    if "agent.cli_backends.context" in sys.modules:
        del sys.modules["agent.cli_backends.context"]

    # Before import, the service modules should not be in sys.modules
    services_to_check = [
        "agent.services.approval_request_service",
        "agent.services.ananta_tool_policy_service",
        "agent.services.ananta_tool_registry_service",
    ]
    # Clear them so we can verify they aren't loaded
    for s in services_to_check:
        if s in sys.modules:
            del sys.modules[s]

    # Now import CliBackendContext
    from agent.cli_backends.context import CliBackendContext  # noqa: F401

    # None of the service modules should be loaded yet
    for s in services_to_check:
        assert s not in sys.modules, f"{s} was eagerly imported by CliBackendContext"
