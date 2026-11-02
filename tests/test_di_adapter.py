"""Tests for agent.services.di — the canonical repository DI seam.

CLN-030 reduced the module to the sanctioned surface: get_repository_registry()
plus the one factory with a live consumer. These tests verify the DIP
guarantee (call-time lookup through the registry), substitutability (LSP) and
that the seam stays narrow (no factory sprawl returns).
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_di_module_imports():
    from agent.services import di  # noqa: F401

    assert hasattr(di, "get_memory_entry_repository")
    assert hasattr(di, "get_repository_registry")


def test_get_memory_entry_repository_returns_singleton():
    from agent.services.di import get_memory_entry_repository

    repo_a = get_memory_entry_repository()
    repo_b = get_memory_entry_repository()
    assert repo_a is repo_b  # idempotent call-time lookup


def test_get_memory_entry_repository_uses_late_binding(monkeypatch):
    """The factory must read the registry at call time, not at import time.
    Patching ``agent.repository.memory_entry_repo`` must affect subsequent
    factory calls — this is the DIP guarantee.
    """
    from agent.services.di import get_memory_entry_repository

    class _FakeRepo:
        pass

    fake = _FakeRepo()
    monkeypatch.setattr("agent.repository.memory_entry_repo", fake)
    assert get_memory_entry_repository() is fake


def test_registry_accessor_is_swappable(monkeypatch):
    """Patching the registry accessor itself is the coarse-grained seam."""
    import agent.services.di as di

    fake_registry = MagicMock()
    monkeypatch.setattr(di, "get_repository_registry", lambda: fake_registry)
    assert di.get_memory_entry_repository() is fake_registry.memory_entry_repo


def test_registry_returns_agent_repository_module():
    import agent.repository as repository_module
    from agent.services.di import get_repository_registry

    assert get_repository_registry() is repository_module


def test_module_surface_stays_narrow():
    """CLN-030 guard: the dead 59-factory sprawl must not return. The module
    exposes exactly the two seam accessors."""
    from agent.services import di

    accessors = {name for name in dir(di) if name.startswith("get_")}
    assert accessors == {"get_memory_entry_repository", "get_repository_registry"}


def test_factory_substitutability(monkeypatch):
    """LSP: any object with the right protocol can stand in via the registry."""
    from agent.services.di import get_memory_entry_repository

    fake = MagicMock()
    monkeypatch.setattr("agent.repository.memory_entry_repo", fake)
    assert get_memory_entry_repository() is fake
