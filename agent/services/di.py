"""Canonical DI seam for call-time repository resolution.

This module was previously a 59-factory adapter layer of which exactly one
factory had a production consumer (CLN-030). The dead surface is gone; what
remains is the sanctioned seam:

- ``get_repository_registry()`` — returns the repository registry module
  (``agent.repository``) that owns the singleton repositories. It is the
  canonical injection point: tests substitute fakes by monkeypatching
  ``agent.repository.<repo_name>`` (or this function) and production code
  re-reads the registry on every call.
- ``get_memory_entry_repository()`` — the one factory with a live consumer
  (``ResultMemoryService``); resolves ``memory_entry_repo`` through the
  registry at call time.

SOLID-Begründung
----------------
- DIP: consumers depend on call-time lookup through the registry instead of
  freezing a module-import reference, so patched or rebound repositories are
  always seen.
- SRP: this module owns the seam only — no domain logic, no SQL, no I/O.
- ISP: a single narrow accessor per actually-used repository; no catch-all
  container of unused factories.

Usage
-----
Production:
    from agent.services.di import get_memory_entry_repository
    repo = get_memory_entry_repository()

Tests (substitution):
    monkeypatch.setattr("agent.repository.memory_entry_repo", fake_repo)
    # or swap the whole seam:
    monkeypatch.setattr("agent.services.di.get_memory_entry_repository", lambda: fake)
"""

from __future__ import annotations

from typing import Any

__all__ = ["get_memory_entry_repository", "get_repository_registry"]


def get_repository_registry() -> Any:
    """Return the canonical repository registry (the ``agent.repository`` module).

    The registry owns the repository singletons. Reading it at call time (not
    at import time) keeps dependency inversion intact: rebinding an attribute
    on the registry is immediately visible to every consumer.
    """
    import agent.repository as registry

    return registry


def get_memory_entry_repository() -> Any:
    """Call-time lookup for the ``memory_entry_repo`` singleton.

    Resolves through ``get_repository_registry()`` on every call so tests can
    monkeypatch ``agent.repository.memory_entry_repo`` (or the registry
    accessor) and the next call returns the fake. Returns the canonical
    singleton in production.
    """
    return getattr(get_repository_registry(), "memory_entry_repo")
